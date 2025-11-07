"""
FastAPI Backend for LLM-based Bug Verification
Handles verification requests from the Flask webhook server
"""
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import httpx
import json
import re

app = FastAPI()

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Gemini API Configuration
# GEMINI_API_KEY = "AIzaSyBNalJJJdA0eDA-8cJ7vu6cHN18Kuij3hg"
GEMINI_API_KEY="AIzaSyBNalJJJdA0eDA-8cJ7vu6cHN18Kuij3hg"
GEMINI_MODEL = "gemini-2.5-flash-preview-09-2025"
GEMINI_API_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"

SYSTEM_INSTRUCTION_TEMPLATE = """
You are a JSON-only code verification bot. Respond ONLY with valid JSON.

Task: Check if code changes fix bug `{bug_to_check}`.

Steps:
1. Find the bug in Bug Documentation
2. Compare the SOLUTION with Code Changes
3. Ignore whitespace/formatting differences
4. Focus on functional correctness

Response format (JSON ONLY):
- If fixed: {{ "{bug_to_check}": "true" }}
- If not fixed: {{ "{bug_to_check}": "false" }}

Be lenient on style, strict on logic.
"""

class VerifyRequest(BaseModel):
    bug_diff_json: str
    bugs_doc: str
    bug_to_check: str

@app.post("/verify")
async def verify_bug_fix(request: VerifyRequest):
    """Verify a bug fix using LLM"""
    if not GEMINI_API_KEY:
        raise HTTPException(status_code=500, detail="GEMINI_API_KEY not configured")

    try:
        # Parse the diff
        parsed_diff = json.loads(request.bug_diff_json)
    except json.JSONDecodeError:
        parsed_diff = {"raw": request.bug_diff_json}
    
    # Use LLM verification
    try:
        pretty_diff = json.dumps(parsed_diff, indent=2)
    except:
        pretty_diff = str(parsed_diff)

    user_prompt = f"""Bug Documentation:
{request.bugs_doc}

Code Changes:
{pretty_diff}

Check bug: {request.bug_to_check}
Response (JSON only): {{ "{request.bug_to_check}": "true/false" }}"""

    system_instruction = SYSTEM_INSTRUCTION_TEMPLATE.format(bug_to_check=request.bug_to_check)

    payload = {
        "contents": [{
            "parts": [{"text": user_prompt}]
        }],
        "systemInstruction": {
            "parts": [{"text": system_instruction}]
        },
        "generationConfig": {
            "temperature": 0.1,
            "maxOutputTokens": 2000
        }
    }

    async with httpx.AsyncClient(timeout=120) as client:
        try:
            response = await client.post(
                GEMINI_API_URL,
                headers={"Content-Type": "application/json"},
                json=payload,
            )
            response.raise_for_status()
            
            data = response.json()
            
            # Extract the response text
            if "candidates" in data and len(data["candidates"]) > 0:
                candidate = data["candidates"][0]
                
                # Check for MAX_TOKENS issue
                finish_reason = candidate.get("finishReason", "")
                if finish_reason == "MAX_TOKENS":
                    print(f"[WARNING] Response hit MAX_TOKENS limit. Returning rejection.")
                    return {"verification_result": f'{{"{request.bug_to_check}": "false"}}', "method": "llm_max_tokens"}
                
                if "content" in candidate and "parts" in candidate["content"] and len(candidate["content"]["parts"]) > 0:
                    content = candidate["content"]["parts"][0]["text"]
                    
                    # Clean the response to ensure it's valid JSON
                    cleaned_content = clean_llm_response(content)
                    
                    return {"verification_result": cleaned_content, "method": "llm"}
                else:
                    print(f"Unexpected response structure: {json.dumps(data, indent=2)}")
                    # Default to rejection if no content
                    return {"verification_result": f'{{"{request.bug_to_check}": "false"}}', "method": "llm_error"}
            else:
                print(f"No candidates in response: {json.dumps(data, indent=2)}")
                return {"verification_result": f'{{"{request.bug_to_check}": "false"}}', "method": "llm_error"}
                
        except httpx.HTTPStatusError as e:
            print(f"HTTP error: {e}")
            print(f"Response body: {e.response.text}")
            raise HTTPException(status_code=e.response.status_code, detail=f"Error from Gemini API: {e.response.text}")
        except Exception as e:
            print(f"An unexpected error occurred: {e}")
            raise HTTPException(status_code=500, detail="An unexpected server error occurred.")

def clean_llm_response(response_text):
    """Extract valid JSON from LLM response"""
    try:
        json.loads(response_text)
        return response_text
    except:
        json_match = re.search(r'\{[^{}]*"[^{}]*"[^{}]*\}', response_text)
        if json_match:
            return json_match.group()
        else:
            return '{"error": "invalid_response"}'

@app.get("/health")
async def health_check():
    return {"status": "healthy", "service": "LLM Verification Backend"}

if __name__ == "__main__":
    import uvicorn
    print("Starting LLM Verification Backend...")
    uvicorn.run(app, host="0.0.0.0", port=8000)
