from flask import Flask, request, jsonify
from flask_cors import CORS
from supabase import create_client
import os
import tempfile
import google.generativeai as genai
import PIL.Image
import whisper
import subprocess
from dotenv import load_dotenv

# Load environment variables
load_dotenv()
url = os.getenv("SUPABASE_URL")
key = os.getenv("SUPABASE_KEY")
gemini_key = os.getenv("GEMINI_API_KEY")

# Initialize clients
supabase = create_client(url, key)
genai.configure(api_key=gemini_key)

app = Flask(__name__)
CORS(app)

def transcribe_audio(file_path):
    model = whisper.load_model("small")
    result = model.transcribe(file_path)
    return result["text"].strip()

def classify_image_with_gemini(image_path):
    try:
        img = PIL.Image.open(image_path)
        model = genai.GenerativeModel('gemini-1.5-flash')
        prompt = (
            "You are an AI for a civic reporting app. "
            "Look at this photo of a public space. "
            "Respond with EXACTLY ONE WORD from this list: "
            "pothole, garbage, broken_light, graffiti, tree_fall, water_leak, other. "
            "If unsure, respond with 'other'."
        )
        response = model.generate_content([prompt, img], safety_settings={
            'HATE': 'BLOCK_NONE',
            'HARASSMENT': 'BLOCK_NONE',
            'SEXUAL': 'BLOCK_NONE',
            'DANGEROUS': 'BLOCK_NONE'
        })
        category = response.text.strip().lower()
        allowed = {"pothole", "garbage", "broken_light", "graffiti", "tree_fall", "water_leak", "other"}
        return category if category in allowed else "other"
    except Exception as e:
        print("Gemini error:", e)
        return "other"

@app.route('/api/issue', methods=['POST'])
def create_issue():
    data = request.json
    file_url = data.get("file_url")
    description = data.get("description", "")
    reported_by = data.get("reported_by")
    lat = data.get("lat")
    lng = data.get("lng")
    
    auto_category = "other"
    auto_priority = "low"

    if file_url:
        file_name = file_url.split("/")[-1]
        with tempfile.NamedTemporaryFile(delete=False, suffix=os.path.splitext(file_name)[1]) as tmp_file:
            local_path = tmp_file.name

        try:
            # Download file from Supabase
            file_data = supabase.storage.from_("media").download(file_name)
            with open(local_path, "wb") as f:
                f.write(file_data)

            # Process based on type
            if file_name.lower().endswith(('.mp3', '.wav', '.m4a', '.ogg')):
                description = transcribe_audio(local_path) or description
            elif file_name.lower().endswith(('.jpg', '.jpeg', '.png')):
                auto_category = classify_image_with_gemini(local_path)
                auto_priority = "high" if auto_category != "other" else "low"
            elif file_name.lower().endswith(('.mp4', '.mov', '.avi')):
                frame_path = local_path + "_frame.jpg"
                # Extract first frame
                subprocess.run([
                    "ffmpeg", "-i", local_path, "-vframes", "1", "-y", frame_path
                ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                if os.path.exists(frame_path):
                    auto_category = classify_image_with_gemini(frame_path)
                    auto_priority = "high" if auto_category != "other" else "low"
                    os.remove(frame_path)

        except Exception as e:
            print("Processing error:", e)
        finally:
            if os.path.exists(local_path):
                os.remove(local_path)

    # Save to DB
    issue_data = {
        "description": description,
        "lat": lat,
        "lng": lng,
        "status": "pending",
        "category": auto_category,
        "priority": auto_priority,
        "file_url": file_url,
        "reported_by": reported_by
    }
    response = supabase.table("issues").insert(issue_data).execute()
    return jsonify(response.data[0]), 201

@app.route('/api/issues', methods=['GET'])
def get_issues():
    response = supabase.table("issues").select("*").execute()
    return jsonify(response.data)

@app.route('/health', methods=['GET'])
def health():
    return jsonify({"status": "OK"})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)