import os
import io
import time
import json
import requests
import asyncio
from google import genai
from google.genai import types
from firecrawl import FirecrawlApp
from fpdf import FPDF
from PIL import Image, ImageDraw, ImageFont
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

class ProductionAgent:
    def __init__(self, progress_callback=None):
        self.firecrawl_key = os.getenv("FIRECRAWL_API_KEY")
        self.google_key = os.getenv("GOOGLE_API_KEY")
        self.progress_callback = progress_callback
        
        if not self.firecrawl_key or not self.google_key:
            error_msg = "❌ Error: Missing API Keys. Please set FIRECRAWL_API_KEY and GOOGLE_API_KEY in Vercel Environment Variables."
            print(error_msg)
            # Server will catch the ValueError and send to client
            raise ValueError(error_msg)
            
        try:
            self.app = FirecrawlApp(api_key=self.firecrawl_key)
            self.client = genai.Client(api_key=self.google_key)
        except Exception as e:
            error_msg = f"❌ Error initializing AI clients: {str(e)}"
            print(error_msg)
            raise ValueError(error_msg)

    async def log(self, message):
        print(message)
        if self.progress_callback:
            if asyncio.iscoroutinefunction(self.progress_callback):
                await self.progress_callback(message)
            else:
                self.progress_callback(message)

    async def run(self, target_url):
        await self.log(f"🚀 Starting analysis for: {target_url}")
        
        # 1. Get Screenshot
        image_bytes = await self.get_screenshot(target_url)
        
        if image_bytes:
            # 2. Analyze & Annotate
            full_data, annotated_bytes = await self.analyze_and_annotate(image_bytes)
            
            await self.log("📄 Generating PDF Report...")
            # 3. Report
            # For Vercel/Serverless, we must write to /tmp
            import tempfile
            tmp_dir = tempfile.gettempdir()
            output_filename = os.path.join(tmp_dir, f"audit_report_{int(time.time())}.pdf")
            
            self.create_pdf_report(target_url, full_data, annotated_bytes, output_filename)
            await self.log(f"✅ Report generated: {output_filename}")
            return output_filename
        else:
            await self.log("❌ Failed to capture screenshot.")
            return None

    async def get_screenshot(self, url):
        """Captures a full-page screenshot and returns the image bytes."""
        await self.log(f"📸 Capturing screenshot of {url}...")
        params = {
            'formats': [
                {"type": "screenshot", "full_page": True}
            ]
        }
        
        try:
            # Firecrawl is synchronous, so we run it in a thread/executor to not block async loop
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(None, lambda: self.app.scrape(url, formats=params['formats']))
            
            screenshot_url = None
            if isinstance(result, dict):
                screenshot_url = result.get('screenshot')
            elif hasattr(result, 'screenshot'):
                screenshot_url = result.screenshot
            
            if not screenshot_url:
                raise Exception("Failed to get screenshot URL from Firecrawl.")
                
            await self.log(f"🔗 Screenshot URL obtained: {screenshot_url}")
            
            # Download the image bytes
            response = requests.get(screenshot_url)
            if response.status_code == 200:
                return response.content
            else:
                raise Exception(f"Failed to download image. Status: {response.status_code}")
                
        except Exception as e:
            await self.log(f"❌ Firecrawl Error: {e}")
            return None

    async def analyze_and_annotate(self, image_bytes):
        """
        Sends image to Gemini, requests structured JSON with layout coordinates,
        and draws "Viral Style" annotations (Yellow Arrows + Text).
        """
        await self.log("🧠 Analyzing image with Gemini Vision...")
        
        try:
            img = Image.open(io.BytesIO(image_bytes))
            if img.mode != 'RGB':
                img = img.convert('RGB')
            width, height = img.size

            prompt = """
            You are an Expert in Trading Psychology for Beginners.
            Your goal is to make this trading platform safe, easy, and welcoming for a NON-TECHNICAL person who is scared of losing money.

            Analyze this landing page screenshot.

            You are a Senior UX/UI Conversion Optimisation Expert.
            
            PART 1: EXECUTIVE SUMMARY
            Write a 1-paragraph summary (approx 50 words) on how to improve this page for Non-Technical Traders.

            PART 2: ANNOTATIONS
            Return an annotated image for UX and UI that can be improved in that page in order for us to increase convergent rate by 10%.
            Identify 5 to 7 critical issues (Full Annotation Mark).
            
            Focus on:
            - **Color Psychology**: Reducing anxiety for beginners.
            - **Trust**: Increasing credibility.
            - **Clarity**: Removing jargon.

            Use the "Clickbait/Viral" visual style (Yellow Arrows + Punchy Text) for the coordinates.

            Return a JSON object:
            {
                "executive_summary": "...",
                "annotations": [
                    {
                        "id": 1,
                        "text": "CHANGE THIS!",  // Short, punchy, uppercase. 
                        "description": "Why this hurts conversion...",
                        "recommendation": "Specific fix to increase conversion by 10%.",
                        "label_pos": [ymin, xmin, ymax, xmax], 
                        "target_pos": [y, x] // The specific point (normalized 0-1000) the arrow should point to
                    }
                ]
            }
            """
            
            # Run Gemini call in executor
            loop = asyncio.get_event_loop()
            
            generation_config = types.GenerateContentConfig(
                response_mime_type="application/json"
            )

            response = await loop.run_in_executor(None, lambda: self.client.models.generate_content(
                model='gemini-2.0-flash',
                contents=[prompt, img],
                config=generation_config
            ))
            
            full_response = json.loads(response.text)
            executive_summary = full_response.get("executive_summary", "No summary provided.")
            analysis_data = full_response.get("annotations", [])
            
            await self.log("🎨 Drawing annotations on image...")

            # Draw Annotations
            draw = ImageDraw.Draw(img, "RGBA")
            
            try:
                # Basic font fallback
                font = ImageFont.load_default() 
                # Ideally use a .ttf if available, but default is safer for portability
            except IOError:
                font = ImageFont.load_default()

            for item in analysis_data:
                label_box = item.get("label_pos")
                target = item.get("target_pos")
                text = item.get("text", "").upper()
                
                if label_box and target:
                    # Parse coordinates
                    l_ymin, l_xmin, l_ymax, l_xmax = label_box
                    t_y, t_x = target
                    
                    # Convert to pixels
                    lx = (l_xmin / 1000) * width
                    ly = (l_ymin / 1000) * height
                    
                    tx = (t_x / 1000) * width
                    ty = (t_y / 1000) * height
                    
                    lcx = ((l_xmin + l_xmax) / 2000) * width
                    lcy = ((l_ymin + l_ymax) / 2000) * height
                    
                    # Draw Arrow (Yellow with Black Outline)
                    draw.line([lcx, lcy, tx, ty], fill="black", width=6)
                    draw.line([lcx, lcy, tx, ty], fill="#FFD700", width=4)
                    
                    # Draw Arrowhead
                    r = 10
                    draw.ellipse([tx-r-2, ty-r-2, tx+r+2, ty+r+2], fill="black")
                    draw.ellipse([tx-r, ty-r, tx+r, ty+r], fill="#FFD700")
                    
                    # Draw Text with Background
                    bbox = draw.textbbox((lx, ly), text, font=font)
                    pad = 5
                    # Ensure bbox is valid
                    if bbox:
                         draw.rectangle([bbox[0]-pad, bbox[1]-pad, bbox[2]+pad, bbox[3]+pad], fill="#FFD700", outline="black", width=2)
                         draw.text((lx, ly), text, font=font, fill="black")

            # Convert annotated image back to bytes
            output_buffer = io.BytesIO()
            img.save(output_buffer, format="PNG")
            annotated_image_bytes = output_buffer.getvalue()
            
            return full_response, annotated_image_bytes

        except Exception as e:
            await self.log(f"❌ Gemini/Annotation Error: {e}")
            return {}, image_bytes

    def create_pdf_report(self, url, full_data, image_bytes, output_filename="report.pdf"):
        """Generates a multi-page PDF report."""
        
        pdf = FPDF()
        
        # --- PAGE 1: Executive Summary ---
        pdf.add_page()
        pdf.set_font("Arial", 'B', 24)
        pdf.cell(0, 20, "Conversion Audit Report", ln=True, align='C')
        
        pdf.set_font("Arial", 'I', 12)
        pdf.cell(0, 10, f"Analysis for: {url}", ln=True, align='C')
        pdf.ln(20)
        
        pdf.set_font("Arial", 'B', 16)
        pdf.cell(0, 10, "Executive Summary", ln=True)
        pdf.ln(5)
        
        pdf.set_font("Arial", size=12)
        exec_summary = full_data.get("executive_summary", "No summary available.")
        # Clean text
        exec_summary = exec_summary.encode('latin-1', 'replace').decode('latin-1')
        pdf.multi_cell(0, 10, exec_summary)
        
        pdf.ln(20)
        pdf.set_font("Arial", 'B', 14)
        pdf.cell(0, 10, "Key Findings Overview:", ln=True)
        pdf.ln(5)
        
        annotations = full_data.get("annotations", [])
        for item in annotations:
            uid = item.get("id")
            title = item.get("text", item.get("title", "Issue"))
            desc = item.get("description", "")
            rec = item.get("recommendation", "No specific recommendation.")
            
            # Sanitize text
            title = str(title).encode('latin-1', 'replace').decode('latin-1')
            desc = str(desc).encode('latin-1', 'replace').decode('latin-1')
            rec = str(rec).encode('latin-1', 'replace').decode('latin-1')
            
            pdf.set_font("Arial", 'B', 12)
            pdf.set_text_color(200, 0, 0) # Dark Red
            pdf.cell(10, 8, f"#{uid}", ln=0)
            pdf.set_text_color(0, 0, 0) # Black
            pdf.cell(0, 8, f"{title}", ln=True)
            
            pdf.set_font("Arial", size=11)
            pdf.multi_cell(0, 6, f"Problem: {desc}")
            
            pdf.set_font("Arial", 'I', 11)
            pdf.set_text_color(0, 100, 0) # Dark Green
            pdf.multi_cell(0, 6, f"Fix: {rec}")
            pdf.set_text_color(0, 0, 0)
            pdf.ln(5)

        # --- PAGE 2: Annotated Visuals ---
        pdf.add_page()
        pdf.set_font("Arial", 'B', 16)
        pdf.cell(0, 10, "Visual Annotations", ln=True, align='C')
        pdf.ln(5)
        
        # Save temp image
        # Use a timestamp to avoid conflicts in concurrent requests (basic implementation)
        timestamp = int(time.time())
        temp_img_path = f"temp_annotated_{timestamp}.png"
        with open(temp_img_path, "wb") as f:
            f.write(image_bytes)
            
        try:
            # 190mm width
            pdf.image(temp_img_path, x=10, y=30, w=190)
        except Exception as e:
            print(f"Error adding image: {e}")

        pdf.output(output_filename)
        
        if os.path.exists(temp_img_path):
            os.remove(temp_img_path)
