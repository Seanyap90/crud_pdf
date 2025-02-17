from files_api.msg_queue import QueueFactory
from files_api.vlm.rag import Worker
from tests.fixtures.rag_fixtures import TEST_QUEUE
import asyncio
import pytest

@pytest.mark.asyncio
async def test_worker_consumes_pdf_standalone():
    
    # Setup
    queue = QueueFactory.get_queue_handler()
    worker = Worker(queue)
    
    # Add test task (simulating what upload would add)
    test_task = {
        "task_type": "process_pdf",
        "file_info": {
            "filepath": "docs/test.pdf"
        }
    }
    queue.local_queue.put_nowait(test_task)
    
    # Start worker and process
    async with asyncio.TaskGroup() as tg:
        worker_task = tg.create_task(worker.listen_for_tasks())
        await asyncio.sleep(2)  # Give time for processing
        
        # Verify queue was consumed
        assert queue.local_queue.empty()
        
        # Clean up
        worker_task.cancel()

@pytest.mark.asyncio
async def test_worker_consumes_pdf(shared_queue):
    print("\nQueue state before processing:")
    # Use the TEST_QUEUE directly to check state
    task = await TEST_QUEUE.get_task()
    print("Found task:", task)
    
    if task:
        await TEST_QUEUE.add_task(task)
        print("Task put back in queue")
    
    worker = Worker(TEST_QUEUE)  # Use TEST_QUEUE for worker
    
    async with asyncio.TaskGroup() as tg:
        worker_task = tg.create_task(worker.listen_for_tasks())
        print("Worker task:", worker_task)
        await asyncio.sleep(2)
        
        print("Queue state after processing")
        final_task = await TEST_QUEUE.get_task()
        print("Final queue check:", final_task)
        assert final_task is None
        worker_task.cancel()