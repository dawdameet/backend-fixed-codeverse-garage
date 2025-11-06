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
from deterministic_verifier import DeterministicVerifier

app = FastAPI()
deterministic_verifier = DeterministicVerifier()

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Gemini API Configuration
GEMINI_API_KEY = "AIzaSyBNalJJJdA0eDA-8cJ7vu6cHN18Kuij3hg"
GEMINI_MODEL = "gemini-2.5-flash-preview-09-2025"
GEMINI_API_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"

SYSTEM_INSTRUCTION_TEMPLATE = """
You are a JSON-only code verification bot. Your response MUST be a single, valid JSON object.
Do not include *any* text, markdown, or explanations.
Your response MUST start with {{ and end with }}.

**Your Task:**
You will be given:
1.  A `Bug Documentation` string with all bugs in a START/END format.
2.  A `Code Diff (JSON)` object showing code changes.
3.  A specific bug key to check: `{bug_to_check}`.

**Step-by-Step Instructions:**
1.  **Find the Bug:** Look in the `Bug Documentation` for the *exact* `START "{bug_to_check}":` block.
2.  **Find the Solution:** Inside that block, find the `SOLUTION:` section.
3.  **Analyze the Diff:** Look at the `Code Diff (JSON)`. Focus on the actual code changes.
4.  **Make a Decision:**
    * Compare the expected `SOLUTION` with the actual code changes in the diff.
    * **Ignore minor whitespace, formatting, and comment differences.**
    * Focus on **functional equivalence** - the logic should be the same.
    * Output `{{ "{bug_to_check}": "true" }}` **if and only if** the code changes implement the solution correctly.
    * Output `{{ "{bug_to_check}": "false" }}` in ALL other cases.

**Important:**
- Be strict about logic but lenient about code style
- Consider different but equivalent implementations as correct
- Only return true if the core bug fix is implemented properly
"""

class VerifyRequest(BaseModel):
    bug_diff_json: str
    bugs_doc: str
    bug_to_check: str

@app.post("/verify")
async def verify_bug_fix(request: VerifyRequest):
    """Verify a bug fix using deterministic rules first, then LLM if needed"""
    if not GEMINI_API_KEY:
        raise HTTPException(status_code=500, detail="GEMINI_API_KEY not configured")

    try:
        # Parse the diff
        parsed_diff = json.loads(request.bug_diff_json)
    except json.JSONDecodeError:
        parsed_diff = {"raw": request.bug_diff_json}
    
    # Try deterministic verification first
    deterministic_result = deterministic_verifier.verify_bug_fix(
        request.bugs_doc, parsed_diff, request.bug_to_check
    )
    
    if deterministic_result is not None:
        result_str = "true" if deterministic_result else "false"
        return {
            "verification_result": json.dumps({request.bug_to_check: result_str}),
            "method": "deterministic"
        }
    
    # Fallback to LLM verification
    try:
        pretty_diff = json.dumps(parsed_diff, indent=2)
    except:
        pretty_diff = str(parsed_diff)

    user_prompt = f"""
**Bug Documentation:**
---
{request.bugs_doc}
---

**Code Changes:**
---
{pretty_diff}
---

**Verification Request:**
Check **ONLY** the bug named `{request.bug_to_check}`.

Respond with JSON only: {{ "{request.bug_to_check}": "true" }} or {{ "{request.bug_to_check}": "false" }}
"""

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
            "maxOutputTokens": 500
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
                if "content" in candidate and "parts" in candidate["content"] and len(candidate["content"]["parts"]) > 0:
                    content = candidate["content"]["parts"][0]["text"]
                    
                    # Clean the response to ensure it's valid JSON
                    cleaned_content = clean_llm_response(content)
                    
                    return {"verification_result": cleaned_content, "method": "llm"}
                else:
                    print(f"Unexpected response structure: {json.dumps(data, indent=2)}")
                    raise HTTPException(status_code=500, detail=f"Unexpected API response structure")
            else:
                print(f"No candidates in response: {json.dumps(data, indent=2)}")
                raise HTTPException(status_code=500, detail="No response from AI model")
                
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
