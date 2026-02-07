from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
import asyncio
import json
import logging
from agent import ProductionAgent

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI()

# Mount static files (for PDF reports)
app.mount("/static", StaticFiles(directory="static"), name="static")

# Templates
templates = Jinja2Templates(directory="templates")

# Store active streaming queues
active_streams = []

class StartRequest(BaseModel):
    url: str

async def progress_generator(url: str):
    """
    Generator that runs the agent and yields SSE events.
    """
    queue = asyncio.Queue()
    
    async def callback(message):
        await queue.put(message)

    try:
        agent = ProductionAgent(progress_callback=callback)
    except Exception as e:
        yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"
        return
    
    # Run agent in background task
    task = asyncio.create_task(agent.run(url))
    
    try:
        while not task.done() or not queue.empty():
            # Wait for next message or task completion
            done, pending = await asyncio.wait(
                [asyncio.create_task(queue.get()), task],
                return_when=asyncio.FIRST_COMPLETED
            )
            
            # If queue has item, yield it
            for t in done:
                if t == task:
                     # Task finished
                     if not queue.empty():
                         continue # Process remaining queue items
                else:
                    message = t.result()
                    yield f"data: {json.dumps({'message': message})}\n\n"
            
            if task.done() and queue.empty():
                break

        # Check result
        result = task.result()
        if result:
            # Construct download URL (assuming result is full path in tmp)
            # We want just the filename
            filename = os.path.basename(result)
            yield f"data: {json.dumps({'type': 'complete', 'report_url': 'api/download/' + filename})}\n\n"
        else:
            yield f"data: {json.dumps({'type': 'error', 'message': 'Analysis failed'})}\n\n"

    except Exception as e:
        yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"

@app.get("/", response_class=HTMLResponse)
async def read_root(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.get("/api/stream")
async def stream_progress(url: str):
    return StreamingResponse(progress_generator(url), media_type="text/event-stream")

import os
from fastapi.responses import FileResponse
import tempfile

@app.get("/api/download/{filename}")
async def download_file(filename: str):
    # Security: Ensure filename is just a basename
    filename = os.path.basename(filename)
    tmp_dir = tempfile.gettempdir()
    file_path = os.path.join(tmp_dir, filename)
    
    if os.path.exists(file_path):
        return FileResponse(file_path, media_type='application/pdf', filename=filename)
    else:
        return {"error": "File not found"}
