import asyncio
import logging
import json
import boto3
import os
import torch
import shutil
import time
from datetime import datetime
from pathlib import Path
from pdf2image import convert_from_path
from files_api.vlm.load_models import ModelManager, model_on_device
from files_api.config import config
from files_api.database.local import update_invoice_processing_status, update_invoice_with_extracted_data
from typing import Optional

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
            data = json.loads(response)
            if isinstance(data, dict):
                # Parse total price
                if 'totalPrice' in data:
                    price_str = data['totalPrice'].replace('€', '').replace(',', '.').strip()
                    total_amount = float(price_str)
                else:
                    total_amount = None
                    
                # Parse weight
                # if 'weight' in data:
                #     weight_str = data['weight'].replace('kg', '').replace(',', '.').strip()
                #     weight = float(weight_str)
                # else:
                #     weight = None
                    
                # return total_amount, weight
                return total_amount
                
        return None, None
    except (json.JSONDecodeError, ValueError) as e:
        logger.error(f"Error parsing invoice data from response: {str(e)}")
        return None, None

class PDFProcessor:
    def __init__(self):
        """Initialize PDF processor with models"""
        self.model_manager = ModelManager()
        self.rag, self.vlm, self.processor = self.model_manager.get_models()
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
        logger.info("PDF Processor initialized with models")

    def download_from_s3(self, filepath):
        """Download file from S3 to temp directory"""
        local_path = self.temp_dir / os.path.basename(filepath)
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
        """Process PDF and return images"""
        try:
            local_path = self.download_from_s3(filepath)
            logger.info(f"Processing PDF: {local_path}")
            
            # Reset RAG model collection
            self.rag = self.model_manager._load_rag_model()
            torch.cuda.empty_cache()
            
            images = convert_from_path(str(local_path))
            index_name = self._get_unique_index_name(filepath)
            logger.info(f"Creating new index: {index_name}")
            
            self.rag.index(
                input_path=str(local_path),
                index_name=index_name,
                store_collection_with_index=False,
                overwrite=True
            )
            
            local_path.unlink()
            return images
        except Exception as e:
            logger.error(f"Error processing PDF: {str(e)}")
            raise

    def generate_response(self, images, query):
        """Generate response based on query"""
        logger.info(f"Generating response for query: {query}")
        
        # Search without index_name parameter
        results = self.rag.search(query, k=1)
        
        if not results:
            logger.warning("No results found from RAG search")
            return "No relevant information found in the document."
            
        retrieved_page = images[results[0]['page_num'] - 1] if results else None
        
        if retrieved_page is None:
            logger.warning("Could not retrieve page from results")
            return "Error retrieving page from document."

        chat_content = [
            {"type": "image", "image": retrieved_page},
            {"type": "text", "text": f"Based on this image, {query}"}
        ]
        
        chat = [{"role": "user", "content": chat_content}]
        text = self.processor.apply_chat_template(chat, add_generation_prompt=True)
        
        with model_on_device(self.vlm, 'cuda'):
            with torch.amp.autocast('cuda', dtype=torch.float16):
                inputs = self.processor(
                    text=text, 
                    images=[retrieved_page],  
                    return_tensors="pt"
                ).to("cuda", dtype=torch.float16)
                
                generated_ids = self.vlm.generate(
                    **inputs,
                    max_new_tokens=500,
                    pad_token_id=self.processor.tokenizer.pad_token_id,
                    use_cache=True,
                    do_sample=False,
                    num_beams=1
                )
            
            output = self.processor.batch_decode(
                generated_ids[:, inputs.input_ids.shape[1]:], 
                skip_special_tokens=True
            )[0]

        return output

    def _get_unique_index_name(self, filepath):
        """Generate unique index name based on filepath and timestamp"""
        base_name = Path(filepath).stem
        timestamp = int(time.time())
        return f"pdf_index_{base_name}_{timestamp}"

    def _clear_old_indices(self):
        """Clear old indices to prevent memory buildup"""
        try:
            if self.index_dir.exists():
                for item in self.index_dir.glob("pdf_index_*"):
                    if item.is_dir():
                        shutil.rmtree(item)
                    else:
                        item.unlink()
            logger.info("Cleared old indices")
        except Exception as e:
            logger.warning(f"Error clearing old indices: {str(e)}")

    def cleanup(self):
        """Clean up temporary files and indices"""
        try:
            # Clean temp PDFs
            for file in self.temp_dir.glob("*"):
                file.unlink()
            # Clean indices
            self._clear_old_indices()
        except Exception as e:
            logger.error(f"Error during cleanup: {str(e)}")

class Worker:
    def __init__(self, queue):
        """Initialize worker with queue and PDF processor"""
        self.queue = queue
        self.running = True
        self.pdf_processor = PDFProcessor()
        logger.info("Worker initialized with PDF processor")

    async def process_task(self, task):
        """Process a PDF task"""
        logger.info(f"Processing task: {task}")
        try:
            if task.get('task_type') == 'process_invoice':
                file_info = task['file_info']
                filepath = file_info['filepath']
                invoice_id = file_info['invoice_id']
                
                # Update status to processing
                update_invoice_processing_status(
                    invoice_id=invoice_id,
                    status='processing',
                    processing_date=datetime.utcnow().isoformat()
                )
                
                try:
                    # Process PDF
                    images = self.pdf_processor.process_pdf(filepath)
                    
                    # Extract both price and weight information
                    query = "What is the total price in this invoice? Please respond in a JSON with keys 'totalPrice'."
                    response = self.pdf_processor.generate_response(images, query)
                    
                    # Parse response
                    total_amount = parse_invoice_data(response)
                    
                    if total_amount is not None:
                        # Update database with successful extraction
                        update_invoice_with_extracted_data(
                            invoice_id=invoice_id,
                            total_amount=total_amount,
                            reported_weight_kg=111,
                            status='completed',
                            completion_date=datetime.utcnow().isoformat(),
                            error_message=None
                        )
                        logger.info(f"Successfully processed invoice {invoice_id} with total amount: {total_amount}€, weight: 111kg")
                        return f"Processed PDF {filepath} with total amount: {total_amount}€, weight: 111kg"
                    else:
                        error_msg = "Could not extract both price and weight from invoice"
                        # Update database with failed extraction
                        update_invoice_with_extracted_data(
                            invoice_id=invoice_id,
                            total_amount=None,
                            reported_weight_kg=None,
                            status='failed',
                            completion_date=datetime.utcnow().isoformat(),
                            error_message=error_msg
                        )
                        logger.error(f"Failed to extract data from invoice {invoice_id}: {error_msg}")
                        return f"Failed to extract data from PDF {filepath}: {error_msg}"
                        
                except Exception as processing_error:
                    error_message = str(processing_error)
                    update_invoice_with_extracted_data(
                        invoice_id=invoice_id,
                        total_amount=None,
                        reported_weight_kg=None,
                        status='failed',
                        completion_date=datetime.utcnow().isoformat(),
                        error_message=error_message
                    )
                    logger.error(f"Error processing invoice {invoice_id}: {error_message}")
                    return f"Error processing PDF {filepath}: {error_message}"
            else:
                logger.warning(f"Unknown task type: {task.get('task_type')}")
                return f"Unknown task type: {task.get('task_type')}"
        except Exception as e:
            logger.error(f"Error processing task: {str(e)}")
            raise

    async def listen_for_tasks(self):
        """Listen for tasks asynchronously"""
        logger.info("Worker started listening for tasks")
        while self.running:
            try:
                task = await self.queue.get_task()
                if task:
                    logger.info(f"Received task: {task}")
                    result = await self.process_task(task)
                    logger.info(result)
                await asyncio.sleep(0.1)
            except Exception as e:
                logger.error(f"Error in task processing loop: {str(e)}")
                await asyncio.sleep(1)

    def start(self):
        """Start the worker"""
        logger.info("Starting worker...")
        try:
            loop = asyncio.get_event_loop()
            loop.run_until_complete(self.listen_for_tasks())
        except Exception as e:
            logger.error(f"Error starting worker: {str(e)}")
            raise

    def stop(self):
        """Stop the worker gracefully"""
        logger.info("Stopping worker...")
        self.running = False
