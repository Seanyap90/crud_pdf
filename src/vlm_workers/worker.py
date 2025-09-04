import asyncio
import logging
import json
import boto3
import os
import torch
import gc
import shutil
import time
import uuid
from datetime import datetime
from pathlib import Path
from pdf2image import convert_from_path
from vlm_workers.models.manager import ModelManager, model_on_device
from src.files_api.config import config
from files_api.services.database import get_invoice_service
#from vlm_workers.scaling.auto_scaler import get_task_manager
from typing import Optional, List, Tuple, Dict, Any
from transformers import GenerationConfig

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def parse_invoice_data(response: str) -> tuple[Optional[float], Optional[float]]:
    """Parse both weight and price from the VLM response."""
    try:
        if isinstance(response, str):
            # Handle error responses early
            if response.startswith("Error generating invoice data"):
                logger.error(f"VLM returned error: {response}")
                return None, None
                
            # Try to extract JSON even if it's embedded in text
            json_start = response.find('{')
            json_end = response.rfind('}') + 1
            
            if json_start >= 0 and json_end > json_start:
                json_str = response[json_start:json_end]
                try:
                    data = json.loads(json_str)
                    if isinstance(data, dict):
                        # Parse total price
                        total_amount = None
                        if 'totalPrice' in data:
                            # Handle both string and numeric values
                            if isinstance(data['totalPrice'], (int, float)):
                                total_amount = float(data['totalPrice'])
                            else:
                                price_str = str(data['totalPrice']).replace('€', '').replace('$', '').replace(',', '.').strip()
                                try:
                                    total_amount = float(price_str)
                                except ValueError:
                                    logger.warning(f"Could not convert price '{price_str}' to float")
                                
                        # Parse weight
                        weight = None
                        if 'weight' in data:
                            # Handle both string and numeric values
                            if isinstance(data['weight'], (int, float)):
                                weight = float(data['weight'])
                            else:
                                weight_str = str(data['weight']).replace('kg', '').replace(',', '.').strip()
                                try:
                                    weight = float(weight_str)
                                except ValueError:
                                    logger.warning(f"Could not convert weight '{weight_str}' to float")
                                
                        return total_amount, weight
                except json.JSONDecodeError:
                    logger.warning(f"Could not parse JSON from substring: {json_str}")

        # Fallback method if JSON parsing fails
        logger.info("Attempting fallback parsing of response")
        lines = response.split('\n')
        total_amount = None
        weight = None
        
        for line in lines:
            if 'price' in line.lower() or 'total' in line.lower() or 'amount' in line.lower():
                # Extract numbers from this line
                import re
                numbers = re.findall(r'\d+\.?\d*', line)
                if numbers:
                    try:
                        total_amount = float(numbers[0])
                    except ValueError:
                        pass
                        
            if 'weight' in line.lower() or 'kg' in line.lower():
                # Extract numbers from this line
                import re
                numbers = re.findall(r'\d+\.?\d*', line)
                if numbers:
                    try:
                        weight = float(numbers[0])
                    except ValueError:
                        pass
        
        return total_amount, weight

    except Exception as e:
        logger.error(f"Error parsing invoice data from response: {str(e)}")
        return None, None

class PDFProcessor:
    def __init__(self):
        """Initialize PDF processor with models"""
        # Share the model manager instance but don't create models yet
        self.model_manager = ModelManager()
        
        # Don't initialize models immediately
        self.rag = None
        self.vlm = None
        self.processor = None
        
        # Initialize S3 client and directories
        self.s3_client = boto3.client(
            's3',
            endpoint_url=config.AWS_ENDPOINT_URL,
            aws_access_key_id=config.AWS_ACCESS_KEY_ID,
            aws_secret_access_key=config.AWS_SECRET_ACCESS_KEY
        )
        
        # Create temp directory for PDFs
        self.temp_dir = Path("temp_pdfs")
        self.temp_dir.mkdir(exist_ok=True)
        
        # Create directory for indices
        self.index_dir = Path(".byaldi")
        self.index_dir.mkdir(exist_ok=True)

        # Track current index
        self.current_index_name = None
        
        logger.info("PDF Processor initialized")

    def download_from_s3(self, filepath):
        """Download file from S3 to temp directory"""
        local_path = self.temp_dir / f"{uuid.uuid4().hex}_{os.path.basename(filepath)}"
        logger.info(f"Downloading {filepath} from S3 to {local_path}")
        
        try:
            self.s3_client.download_file(
                Bucket=config.S3_BUCKET_NAME,
                Key=filepath,
                Filename=str(local_path)
            )
            logger.info(f"Successfully downloaded file to {local_path}")
            return local_path
        except Exception as e:
            logger.error(f"Error downloading from S3: {str(e)}")
            raise

    def process_pdf(self, filepath):
        """Process PDF with memory-efficient approach"""
        local_path = None
        images = None
        
        try:
            # Download the file
            local_path = self.download_from_s3(filepath)
            logger.info(f"Processing PDF: {local_path}")
            
            # IMPORTANT: Create a completely new RAG model instance for each PDF
            # This ensures no document ID collision occurs
            logger.info("Creating fresh RAG model instance to avoid index conflicts")
            from byaldi import RAGMultiModalModel
            
            # Force cleanup before creating new model
            if self.rag is not None:
                del self.rag
                gc.collect()
                torch.cuda.empty_cache()
            
            # Create a completely new model instance using the model manager
            # This ensures proper offline/cache handling in container environments
            if hasattr(self.model_manager, 'create_new_rag_model'):
                # Use ContainerModelLoader's method that handles offline mode properly
                self.rag = self.model_manager.create_new_rag_model()
            else:
                # Fallback for local development mode
                self.rag = self.model_manager.get_rag_model()
            
            # Clean old indices first to prevent buildup
            self._clear_old_indices()
            
            # Convert PDF to images with memory-efficient settings
            images = convert_from_path(
                str(local_path),
                dpi=500,  # Lower DPI to save memory
                thread_count=1,  # Single thread is more memory efficient
                use_pdftocairo=True,  # Generally more memory-efficient than pdf2image
                grayscale=False
            )
            
            # Create unique index name with additional randomness
            unique_id = uuid.uuid4().hex
            timestamp = int(time.time())
            index_name = f"pdf_index_{Path(filepath).stem}_{timestamp}_{unique_id}"
            self.current_index_name = index_name
            logger.info(f"Creating new index: {index_name}")
            
            # Index with unique name to avoid collisions
            self.rag.index(
                input_path=str(local_path),
                index_name=index_name,
                store_collection_with_index=True,
                overwrite=True
            )
            
            # Force garbage collection
            gc.collect()
            
            return images
        except Exception as e:
            logger.error(f"Error processing PDF: {str(e)}", exc_info=True)
            # Clean up images if there's an error
            if images:
                del images
            gc.collect()
            torch.cuda.empty_cache()
            raise
        finally:
            # Clean up the downloaded file
            if local_path and local_path.exists():
                try:
                    local_path.unlink()
                except Exception as e:
                    logger.warning(f"Could not delete temp file {local_path}: {str(e)}")

    def generate_response(self, images: List, query: str) -> str:
        """Generate response based on query with guaranteed single inference"""
        logger.info(f"Generating response for query: {query}")
        
        # Track inference to ensure it happens only once
        inference_completed = False
        result = None
        
        try:
            # Load VLM and processor if needed, without using a non-existent lock
            if self.vlm is None or self.processor is None:
                logger.info("Lazily loading VLM model")
                self.vlm, self.processor = self.model_manager.get_vlm_model()
                if self.vlm is None or self.processor is None:
                    return "Error: VLM model could not be loaded."
            
            # Perform RAG search once and cache results
            page_num = 0
            try:
                logger.info(f"Performing single RAG search with index: {self.current_index_name}")
                results = self.rag.search(query, k=1)
                
                if results:
                    try:
                        page_num = results[0]['page_num'] - 1
                        if page_num < 0 or page_num >= len(images):
                            page_num = 0
                    except Exception as e:
                        logger.warning(f"Error extracting page number: {str(e)}")
                else:
                    logger.warning("No results from RAG search, using first page")
            except Exception as e:
                logger.warning(f"Error in RAG search: {str(e)}")
            
            # Process single page for efficiency
            retrieved_page = images[page_num].copy()
            
            # Prepare inputs
            chat_content = [
                {"type": "image", "image": retrieved_page},
                {"type": "text", "text": f"Based on this image, {query}"}
            ]
            chat = [{"role": "user", "content": chat_content}]
            
            # Apply template once
            text = self.processor.apply_chat_template(chat, add_generation_prompt=True)
            
            # Free page memory
            retrieved_page = None
            
            # Prepare for inference
            device = "cuda" if torch.cuda.is_available() else "cpu"
            logger.info(f"Preparing for inference on {device}")
            
            # Process inputs once
            inputs = self.processor(
                text=text,
                images=[images[page_num]],  # Use original image directly
                return_tensors="pt"
            )
            
            # Configure generation once
            gen_cfg = GenerationConfig(
                max_new_tokens=500,
                pad_token_id=self.processor.tokenizer.pad_token_id,
                use_cache=True,
                cache_implementation="offloaded",  # FIXED: Using "offloaded" for memory efficiency
                do_sample=False,
                num_beams=1
            )
            
            # Move inputs to device
            cuda_inputs = {k: v.to(device) for k, v in inputs.items()}
            
            # Run inference ONCE with proper logging
            logger.info("Starting VLM inference (single operation)")
            with torch.no_grad():
                if not inference_completed:  # Extra safety check
                    generated_ids = self.vlm.generate(
                        **cuda_inputs,
                        generation_config=gen_cfg
                    )
                    
                    # Process output once
                    if 'input_ids' in inputs:
                        result = self.processor.batch_decode(
                            generated_ids[:, inputs['input_ids'].shape[1]:],
                            skip_special_tokens=True
                        )[0]
                    else:
                        result = self.processor.batch_decode(
                            generated_ids,
                            skip_special_tokens=True
                        )[0]
                    
                    inference_completed = True
                    logger.info("VLM inference completed successfully (single pass)")
            
            # Clean up resources
            del inputs, cuda_inputs, generated_ids
            torch.cuda.empty_cache()
            gc.collect()
            
            return result
            
        except Exception as e:
            logger.error(f"Error during single inference: {str(e)}", exc_info=True)
            return f"Error processing document: {str(e)[:100]}..."
        
        finally:
            # Always clean up
            torch.cuda.empty_cache()
            gc.collect()

    def _clear_old_indices(self):
        """Clear old indices to prevent disk space and memory issues"""
        try:
            if not self.index_dir.exists():
                return
                
            # Get all index directories
            index_dirs = []
            for item in self.index_dir.glob("pdf_index_*"):
                if item.is_dir():
                    index_dirs.append(item)
                
            logger.info(f"Found {len(index_dirs)} index directories")
            
            # Calculate how many to remove (keep at most 2)
            max_to_keep = 2  # Reduced to save disk space
            num_to_remove = max(0, len(index_dirs) - max_to_keep)
            
            if num_to_remove <= 0:
                logger.info(f"No old indices to remove. Keeping all {len(index_dirs)} indices.")
                return
                
            # Sort by modification time (oldest first)
            index_dirs.sort(key=lambda x: x.stat().st_mtime)
            
            # Remove oldest indices
            removed_count = 0
            for i, old_dir in enumerate(index_dirs):
                if i >= num_to_remove:
                    break
                    
                try:
                    logger.info(f"Removing old index: {old_dir}")
                    if old_dir.is_dir():
                        shutil.rmtree(old_dir)
                        removed_count += 1
                except Exception as e:
                    logger.warning(f"Failed to remove index {old_dir}: {str(e)}")
            
            logger.info(f"Cleared {removed_count} old indices, kept {len(index_dirs) - removed_count} most recent")
            
            # Force garbage collection
            gc.collect()
                
        except Exception as e:
            logger.error(f"Error during index cleanup: {str(e)}", exc_info=True)

    def cleanup(self):
        """Clean up temporary files and indices"""
        try:
            # Clean temp PDFs
            for file in self.temp_dir.glob("*"):
                try:
                    if file.is_file():
                        file.unlink()
                    elif file.is_dir():
                        shutil.rmtree(file)
                except Exception as e:
                    logger.warning(f"Error removing temp file {file}: {str(e)}")
                    
            # Clean old indices (keep 2 most recent)
            self._clear_old_indices()

            if self.rag is not None:
                del self.rag
                self.rag = None
                logger.info("Unloaded RAG model")
            
            # Just clear GPU memory
            torch.cuda.empty_cache()
            gc.collect()
            
            logger.info("Cleanup completed successfully")
        except Exception as e:
            logger.error(f"Error during cleanup: {str(e)}")

class Worker:
    def __init__(self, queue):
        """Initialize worker with queue"""
        self.queue = queue
        self.running = True
        # Pre-initialize the PDFProcessor to share the same model instance
        self.pdf_processor = PDFProcessor()
        # Initialize scaling manager for scale-to-zero functionality
        #self.scaling_manager = get_task_manager()
        #self.scaling_manager.register_current_task()
        logger.info("Worker initialized with shared PDFProcessor and scaling manager")

    async def process_task(self, task):
        """Process a PDF task reusing the same processor"""
        logger.info(f"Processing task: {task}")
        
        # Always add a small delay between tasks to ensure cleanup
        await asyncio.sleep(1)
        
        try:
            if task.get('task_type') == 'process_invoice':
                file_info = task['file_info']
                filepath = file_info['filepath']
                invoice_id = file_info['invoice_id']
                
                # Force garbage collection before starting a new task
                torch.cuda.empty_cache()
                gc.collect()
                
                # Get invoice service for status updates
                invoice_service = get_invoice_service()
                
                # Update status to processing via HTTP API
                from files_api.adapters.storage import update_task_status, init_storage
                # Initialize storage adapter for HTTP calls
                init_storage()
                logger.info(f"Updating invoice {invoice_id} status to 'processing' via HTTP API")
                update_task_status(invoice_id, 'processing')
                
                try:
                    # Process PDF
                    images = self.pdf_processor.process_pdf(filepath)
                    
                    # Extract both price and weight information
                    query = "What is the total price and weight in this invoice? Please respond in a JSON with keys 'totalPrice' and 'weight'. For weight include the unit 'kg'"
                    response = self.pdf_processor.generate_response(images, query)
                    
                    # Free memory
                    images = None
                    gc.collect()
                    
                    # Parse response
                    total_amount, reported_weight = parse_invoice_data(response)
                    
                    if total_amount is not None and reported_weight is not None:
                        # Update database with successful extraction via HTTP API
                        from files_api.adapters.storage import update_task_result, init_storage
                        init_storage()
                        logger.info(f"Updating invoice {invoice_id} result to 'completed' via HTTP API")
                        update_task_result(
                            task_id=invoice_id,
                            result_data={'total_amount': total_amount, 'reported_weight': reported_weight},
                            status='completed'
                        )
                        logger.info(f"Successfully processed invoice {invoice_id} with total amount: ${total_amount}, weight: {reported_weight}kg")
                        return f"Processed PDF {filepath} with total amount: ${total_amount}, weight: {reported_weight}kg"
                    else:
                        error_msg = f"Could not extract both price and weight from invoice. Response was: {response[:200]}..."
                        # Update database with failed extraction via HTTP API
                        from files_api.adapters.storage import update_task_result, init_storage
                        init_storage()
                        logger.info(f"Updating invoice {invoice_id} result to 'failed' via HTTP API")
                        update_task_result(
                            task_id=invoice_id,
                            result_data=None,
                            status='failed',
                            error_message=error_msg
                        )
                        logger.error(f"Failed to extract data from invoice {invoice_id}: {error_msg}")
                        return f"Failed to extract data from PDF {filepath}: {error_msg}"
                        
                except Exception as processing_error:
                    # Get detailed error
                    import traceback
                    error_traceback = traceback.format_exc()
                    error_message = f"{str(processing_error)}\n{error_traceback}"
                    
                    # Log error
                    logger.error(f"Error processing invoice {invoice_id}: {error_message}", exc_info=True)
                    
                    # Update database via HTTP API
                    from files_api.adapters.storage import update_task_result, init_storage
                    init_storage()
                    logger.info(f"Updating invoice {invoice_id} result to 'failed' via HTTP API (exception)")
                    update_task_result(
                        task_id=invoice_id,
                        result_data=None,
                        status='failed',
                        error_message=str(processing_error)[:500]
                    )
                    
                    return f"Error processing PDF {filepath}: {str(processing_error)}"
            else:
                logger.warning(f"Unknown task type: {task.get('task_type')}")
                return f"Unknown task type: {task.get('task_type')}"
                
        except Exception as e:
            logger.error(f"Error processing task: {str(e)}", exc_info=True)
            raise
        finally:
            # Clean up temporary files but keep the processor instance
            try:
                self.pdf_processor.cleanup()
            except Exception as cleanup_error:
                logger.warning(f"Error during cleanup: {str(cleanup_error)}")
            
            # Unload VLM to free memory but keep RAG model
            try:
                model_manager = ModelManager()
                model_manager.unload_vlm()
                
                # Clear VLM reference in processor but keep RAG
                self.pdf_processor.vlm = None
                self.pdf_processor.processor = None
            except Exception as unload_error:
                logger.warning(f"Error unloading VLM: {str(unload_error)}")
                
            # Force garbage collection
            gc.collect()
            torch.cuda.empty_cache()

    async def listen_for_tasks(self):
        """Listen for tasks asynchronously with scale-to-zero support"""
        logger.info("Worker started listening for tasks")
        consecutive_errors = 0
        
        while self.running:
            try:
                task = await self.queue.get_task()
                if task:
                    logger.info(f"Received task: {task}")
                    result = await self.process_task(task)
                    logger.info(result)
                    consecutive_errors = 0  # Reset error counter on success
                else:
                    # No task received - check if we should scale to zero
                    queue_url = getattr(self.queue, 'queue_url', None)
                    #if queue_url and hasattr(self.scaling_manager, 'scale_to_zero'):
                    #    should_terminate = self.scaling_manager.scale_to_zero(queue_url, grace_period_seconds=30)
                    #    if should_terminate:
                    #        logger.info("Queue empty for 30+ seconds - initiating graceful shutdown")
                    #        self.scaling_manager.initiate_graceful_shutdown("Scale to zero - queue empty")
                    #        self.running = False
                    #        break
                
                await asyncio.sleep(1.0)  # Reduced from 0.1s to 1s to minimize API calls
            except Exception as e:
                consecutive_errors += 1
                logger.error(f"Error in task processing loop: {str(e)}", exc_info=True)
                
                # Implement exponential backoff
                backoff_time = min(30, 2 ** consecutive_errors)
                logger.warning(f"Backing off for {backoff_time} seconds after error...")
                await asyncio.sleep(backoff_time)
                
                # Reset processor if we have multiple errors
                if consecutive_errors >= 3:
                    try:
                        logger.warning("Multiple consecutive errors - reinitializing PDF processor")
                        # Recreate the PDF processor
                        self.pdf_processor = PDFProcessor()
                    except Exception as reset_error:
                        logger.error(f"Error reinitializing PDF processor: {str(reset_error)}")

    def stop(self):
        """Stop the worker gracefully"""
        logger.info("Stopping worker...")
        self.running = False
        
        # Clean up the processor
        if hasattr(self, 'pdf_processor') and self.pdf_processor:
            try:
                self.pdf_processor.cleanup()
                self.pdf_processor = None
            except Exception as e:
                logger.warning(f"Error cleaning up processor during stop: {str(e)}")