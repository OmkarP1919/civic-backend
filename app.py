from flask import Flask, request, jsonify
from flask_cors import CORS
from supabase import create_client
import os
import tempfile
import google.generativeai as genai
import PIL.Image
from dotenv import load_dotenv

# Load .env only in development
load_dotenv()

# Initialize Supabase and Gemini
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
genai.configure(api_key=GEMINI_API_KEY)

app = Flask(__name__)
CORS(app)

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
    description = data.get("description")
    reported_by = data.get("reported_by")
    lat = data.get("lat")
    lng = data.get("lng")
    file_url = data.get("file_url")

    if not description or not reported_by:
        return jsonify({"error": "description and reported_by are required"}), 400

    auto_category = "other"
    auto_priority = "low"

    if file_url:
        try:
            file_name = file_url.split("/")[-1]
            if file_name.lower().endswith(('.jpg', '.jpeg', '.png')):
                with tempfile.NamedTemporaryFile(delete=False, suffix=os.path.splitext(file_name)[1]) as tmp:
                    local_path = tmp.name

                file_data = supabase.storage.from_("media").download(file_name)
                with open(local_path, "wb") as f:
                    f.write(file_data)

                auto_category = classify_image_with_gemini(local_path)
                auto_priority = "high" if auto_category != "other" else "low"

                os.remove(local_path)
        except Exception as e:
            print("Image processing error:", e)

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

@app.route('/api/operator/location', methods=['POST'])
def update_operator_location():
    data = request.json
    user_id = data.get("user_id")
    lat = data.get("lat")
    lng = data.get("lng")

    if not user_id or lat is None or lng is None:
        return jsonify({"error": "user_id, lat, and lng are required"}), 400

    supabase.table("operators").update({
        "current_lat": lat,
        "current_lng": lng,
        "updated_at": "now()"
    }).eq("user_id", user_id).execute()

    return jsonify({"status": "location updated"}), 200

@app.route('/health', methods=['GET'])
def health():
    return jsonify({"status": "OK"})

# Production-ready entry (Render uses gunicorn)
if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
