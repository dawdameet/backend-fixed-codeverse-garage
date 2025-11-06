"""
Flask Webhook Server for GitHub Hackathon Tracker
Receives GitHub webhooks, extracts bug fixes, and verifies them using LLM backend
"""
from flask import Flask, request, jsonify
from flask_cors import CORS
import json
import os
from datetime import datetime
import re
import subprocess
import requests
import tempfile

app = Flask(__name__)
CORS(app)

# Configuration
LLM_BACKEND_URL = "http://localhost:8000/verify"

class HackathonTracker:
    def __init__(self):
        self.setup_tracking_repo()

    def setup_tracking_repo(self):
        """Initialize directory structure and leaderboard"""
        os.makedirs("teams", exist_ok=True)
        os.makedirs("verification", exist_ok=True)
        if not os.path.exists("leaderboard.json"):
            with open("leaderboard.json", "w", encoding="utf-8") as f:
                json.dump([], f)

    def get_bug_points(self, full_repo_name, bug_id):
        """Fetch bug points from GitHub issue labels"""
        try:
            result = subprocess.run(
                [
                    "gh", "issue", "view", str(bug_id),
                    "-R", full_repo_name,
                    "--json", "labels"
                ],
                capture_output=True, text=True, check=True, timeout=10
            )
            labels = [lbl["name"].lower() for lbl in json.loads(result.stdout).get("labels", [])]

            if "difficulty-easy" in labels:
                return 5
            if "difficulty-medium" in labels:
                return 10
            if "difficulty-hard" in labels:
                return 15
            return 0
        except Exception as e:
            print(f"[ERROR] Failed to get points for Bug #{bug_id}: {e}")
            return 0

    def extract_code_changes(self, full_repo_name, commit_hash):
        """Extract code changes from a commit using git"""
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                repo_url = f"https://github.com/{full_repo_name}.git"
                
                # Clone the repo
                subprocess.run([
                    'git', 'clone', '--quiet', repo_url, temp_dir
                ], check=True, capture_output=True, timeout=60)
                
                # Get the commit diff
                result = subprocess.run([
                    'git', 'show', '--format=', '--unified=3', commit_hash
                ], cwd=temp_dir, capture_output=True, text=True, check=True, timeout=30)
                
                changes = self.parse_git_diff(result.stdout)
                return changes
                
        except subprocess.TimeoutExpired:
            print(f"[ERROR] Timeout extracting changes from {full_repo_name}")
            return None
        except subprocess.CalledProcessError as e:
            print(f"[ERROR] Git command failed for {full_repo_name}: {e}")
            return None
        except Exception as e:
            print(f"[ERROR] Unexpected error extracting changes: {e}")
            return None

    def parse_git_diff(self, diff_output):
        """Parse git diff output to extract structured changes"""
        changes = []
        current_file = None
        current_hunk = None
        
        for line in diff_output.split('\n'):
            if line.startswith('diff --git'):
                # Save previous file
                if current_file and current_hunk and (current_hunk["added"] or current_hunk["removed"]):
                    current_file["changes"].append(current_hunk)
                if current_file and current_file["changes"]:
                    changes.append(current_file)
                
                # New file section
                current_file = {
                    "filename": line.split(' b/')[-1] if ' b/' in line else "unknown",
                    "changes": []
                }
                current_hunk = {"added": [], "removed": []}
                
            elif line.startswith('+++') or line.startswith('---'):
                continue
                
            elif line.startswith('+') and not line.startswith('+++'):
                if current_hunk:
                    current_hunk["added"].append(line[1:])
                    
            elif line.startswith('-') and not line.startswith('---'):
                if current_hunk:
                    current_hunk["removed"].append(line[1:])
                    
            elif line.startswith('@@'):
                # New hunk - save previous hunk if exists
                if current_hunk and (current_hunk["added"] or current_hunk["removed"]):
                    current_file["changes"].append(current_hunk)
                current_hunk = {"added": [], "removed": []}
        
        # Add the last file and hunk
        if current_hunk and (current_hunk["added"] or current_hunk["removed"]):
            current_file["changes"].append(current_hunk)
        if current_file and current_file["changes"]:
            changes.append(current_file)
            
        return changes

    def get_bug_description(self, domain, bug_id):
        """Get bug description from domain JSON files"""
        try:
            bugs_file = f"domains/{domain}/bugs.json"
            if os.path.exists(bugs_file):
                with open(bugs_file, 'r', encoding='utf-8') as f:
                    bugs_data = json.load(f)
                    for bug in bugs_data:
                        if bug['id'] == bug_id:
                            return bug
            return None
        except Exception as e:
            print(f"[ERROR] Failed to load bug description: {e}")
            return None

    def verify_with_llm(self, code_changes, bug_description, bug_id):
        """Send code changes to LLM backend for verification"""
        try:
            # Prepare the data for LLM backend
            bug_diff_json = json.dumps({
                "changed": code_changes,
                "files": code_changes
            })
            
            # Convert bug description to string format
            bugs_doc = f"""
START "BUG{bug_id}":
DESCRIPTION: {bug_description.get('description', '')}
EXPECTED: {bug_description.get('expected', '')}
CURRENT: {bug_description.get('current', '')}
FILES: {bug_description.get('files', '')}
SOLUTION: {bug_description.get('solution', 'Fix the code as described')}
END "BUG{bug_id}"
"""
            
            # Send to LLM backend
            response = requests.post(
                LLM_BACKEND_URL,
                json={
                    "bug_diff_json": bug_diff_json,
                    "bugs_doc": bugs_doc,
                    "bug_to_check": f"BUG{bug_id}"
                },
                headers={"Content-Type": "application/json"},
                timeout=60
            )
            
            if response.status_code == 200:
                result = response.json()
                return result.get("verification_result", "{}"), result.get("method", "unknown")
            else:
                print(f"[ERROR] LLM backend returned {response.status_code}: {response.text}")
                return "{}", "error"
                
        except Exception as e:
            print(f"[ERROR] LLM verification failed: {e}")
            return "{}", "error"

    def log_bug_fix(self, team_id, bug_id, domain, commit_hash, commit_message, full_repo_name):
        """Log a bug fix submission and verify it"""
        team_dir = f"teams/{team_id}"
        os.makedirs(f"{team_dir}/bug-fixes", exist_ok=True)

        points = self.get_bug_points(full_repo_name, bug_id)

        # Extract code changes
        print(f"[INFO] Extracting code changes for Bug #{bug_id}...")
        code_changes = self.extract_code_changes(full_repo_name, commit_hash)
        
        # Get bug description
        bug_description = self.get_bug_description(domain, bug_id)
        
        # LLM verification result
        llm_verified = False
        llm_result = "pending"
        verification_method = "none"
        
        if code_changes and bug_description:
            try:
                llm_result, verification_method = self.verify_with_llm(code_changes, bug_description, bug_id)
                
                # Parse LLM response to check if verified
                if f'"BUG{bug_id}": "true"' in llm_result or f'"BUG{bug_id}":"true"' in llm_result:
                    llm_verified = True
                    print(f"[✓] Team {team_id} Bug #{bug_id} verified ({verification_method})")
                else:
                    print(f"[✗] Team {team_id} Bug #{bug_id} rejected ({verification_method})")
                    
            except Exception as e:
                print(f"[ERROR] Verification failed for Team {team_id} Bug #{bug_id}: {e}")
                llm_result = f"error: {str(e)}"
        else:
            if not code_changes:
                print(f"[ERROR] No code changes extracted for Bug #{bug_id}")
            if not bug_description:
                print(f"[ERROR] No bug description found for Bug #{bug_id} in domain {domain}")

        submission = {
            "team_id": team_id,
            "bug_id": bug_id,
            "domain": domain,
            "commit_hash": commit_hash,
            "commit_message": commit_message,
            "submission_time": datetime.now().isoformat(),
            "status": "submitted",
            "verified": llm_verified,
            "points": points if llm_verified else 0,
            "code_changes": code_changes,
            "llm_verification": llm_result,
            "llm_verified": llm_verified,
            "verification_method": verification_method
        }

        with open(f"{team_dir}/bug-fixes/bug-{bug_id}.json", "w", encoding="utf-8") as f:
            json.dump(submission, f, indent=2)

        # Update progress
        self.update_progress(team_id)

        print(f"[LOG] {team_id} → Bug #{bug_id} ({points} pts) - Verified: {llm_verified}")

    def update_progress(self, team_id):
        """Update team progress and leaderboard"""
        team_dir = f"teams/{team_id}"
        bug_files = [
            f for f in os.listdir(f"{team_dir}/bug-fixes")
            if f.endswith(".json")
        ]

        progress = {
            "team_id": team_id,
            "total_submissions": len(bug_files),
            "verified_submissions": 0,
            "total_points": 0,
            "last_submission": None,
            "submissions": [],
            "domain": "unknown"
        }

        for bf in bug_files:
            with open(f"{team_dir}/bug-fixes/{bf}", "r", encoding="utf-8") as f:
                sub = json.load(f)
                progress["submissions"].append(sub)
                progress["domain"] = sub.get("domain", progress["domain"])

                if sub["verified"]:
                    progress["verified_submissions"] += 1
                    progress["total_points"] += sub["points"]

                ts = sub["submission_time"]
                if not progress["last_submission"] or ts > progress["last_submission"]:
                    progress["last_submission"] = ts

        # Write progress
        with open(f"{team_dir}/progress.json", "w", encoding="utf-8") as f:
            json.dump(progress, f, indent=2)

        # Update leaderboard
        self.update_leaderboard()

    def update_leaderboard(self):
        """Update the global leaderboard"""
        leaderboard = []

        for td in os.listdir("teams"):
            prog_path = f"teams/{td}/progress.json"
            if not os.path.isfile(prog_path):
                continue
            with open(prog_path, "r", encoding="utf-8") as f:
                prog = json.load(f)
                leaderboard.append({
                    "team_id": prog["team_id"],
                    "bugs_solved": prog["verified_submissions"],
                    "total_points": prog["total_points"],
                    "last_submission": prog["last_submission"],
                    "domain": prog.get("domain", "unknown")
                })

        # Sort by points (descending), then by earliest submission
        leaderboard.sort(key=lambda x: (-x["total_points"], x["last_submission"] or ""))

        for i, entry in enumerate(leaderboard, 1):
            entry["rank"] = i

        with open("leaderboard.json", "w", encoding="utf-8") as f:
            json.dump(leaderboard, f, indent=2)

        print(f"[LEADERBOARD] {len(leaderboard)} team(s) updated")


tracker = HackathonTracker()


# ------------------------------------------------------------------
# Webhook endpoint
# ------------------------------------------------------------------
@app.route('/webhook/github', methods=['POST'])
def handle_github_webhook():
    """Handle GitHub push webhooks"""
    try:
        # Check if it's a push event
        if request.headers.get('X-GitHub-Event') != 'push':
            return jsonify({"status": "ignored", "reason": "not a push event"}), 200

        # Parse JSON payload - GitHub sends with different content types
        if request.is_json:
            payload = request.get_json()
        else:
            # GitHub may send as form data or with charset in content-type
            try:
                payload = json.loads(request.data.decode('utf-8'))
            except:
                # Try form data
                payload = json.loads(request.form.get('payload', '{}'))
                if not payload:
                    payload = request.get_json(force=True)
        
        if not payload:
            print("[ERROR] Empty payload received")
            return jsonify({"error": "Empty payload"}), 400
        
        repo_name = payload.get('repository', {}).get('name')
        full_repo_name = payload.get('repository', {}).get('full_name')
        
        if not repo_name:
            print("[ERROR] No repository name in payload")
            return jsonify({"error": "No repository name"}), 400

        # Only process team repositories
        if not repo_name.startswith('team-'):
            return jsonify({"status": "ignored", "reason": "not a team repo"}), 200
        
        # Extract team info from repo name: team-{team_id}-{domain}
        parts = repo_name.split('-')
        if len(parts) < 3:
            return jsonify({"error": "Invalid team repo name format"}), 400
            
        team_id = parts[1]
        domain = parts[2]

        # Process each commit
        commits = payload.get('commits', [])
        processed = 0
        
        for commit in commits:
            bug_id = extract_bug_id(commit['message'])
            if bug_id:
                tracker.log_bug_fix(
                    team_id=team_id,
                    bug_id=bug_id,
                    domain=domain,
                    commit_hash=commit['id'],
                    commit_message=commit['message'],
                    full_repo_name=full_repo_name
                )
                processed += 1

        return jsonify({
            "status": "processed",
            "team_id": team_id,
            "commits_processed": processed
        }), 200
        
    except Exception as e:
        print(f"[ERROR] Webhook processing failed: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


# ------------------------------------------------------------------
# Manual verification endpoint
# ------------------------------------------------------------------
@app.route('/verify/<team_id>/<int:bug_id>', methods=['POST'])
def manually_verify_bug(team_id, bug_id):
    """Manually trigger LLM verification for a specific bug"""
    try:
        bug_file = f"teams/{team_id}/bug-fixes/bug-{bug_id}.json"
        if not os.path.exists(bug_file):
            return jsonify({"error": "Bug submission not found"}), 404
        
        with open(bug_file, 'r', encoding='utf-8') as f:
            submission = json.load(f)
        
        # Re-verify with LLM
        code_changes = submission.get('code_changes', [])
        bug_description = tracker.get_bug_description(submission['domain'], bug_id)
        
        if code_changes and bug_description:
            llm_response, method = tracker.verify_with_llm(code_changes, bug_description, bug_id)
            
            # Update submission
            llm_verified = f'"BUG{bug_id}": "true"' in llm_response
            submission['llm_verification'] = llm_response
            submission['llm_verified'] = llm_verified
            submission['verified'] = llm_verified
            submission['verification_method'] = method
            submission['points'] = submission.get('points', 0) if llm_verified else 0
            
            with open(bug_file, 'w', encoding='utf-8') as f:
                json.dump(submission, f, indent=2)
            
            # Update progress
            tracker.update_progress(team_id)
            
            return jsonify({
                "verified": llm_verified,
                "llm_response": llm_response,
                "method": method,
                "message": f"Bug #{bug_id} re-verified successfully"
            })
        else:
            return jsonify({"error": "No code changes or bug description available"}), 400
            
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ------------------------------------------------------------------
# Public API endpoints
# ------------------------------------------------------------------
@app.route('/leaderboard')
def get_leaderboard():
    """Get the current leaderboard"""
    try:
        with open('leaderboard.json', 'r', encoding='utf-8') as f:
            return jsonify(json.load(f))
    except Exception:
        return jsonify([])


@app.route('/team/<team_id>')
def get_team_progress(team_id):
    """Get progress for a specific team"""
    path = f"teams/{team_id}/progress.json"
    if os.path.exists(path):
        with open(path, 'r', encoding='utf-8') as f:
            return jsonify(json.load(f))
    return jsonify({"error": "Team not found"}), 404


@app.route('/health')
def health_check():
    """Health check endpoint"""
    return jsonify({"status": "healthy", "service": "GitHub Hackathon Tracker"})


def extract_bug_id(message):
    """Extract bug ID from commit message"""
    patterns = [
        r'bug[#\s]*(\d+)',
        r'fix[#\s]*(\d+)',
        r'#(\d+)',
        r'BUG[#\s]*(\d+)'
    ]
    for p in patterns:
        m = re.search(p, message, re.IGNORECASE)
        if m:
            return int(m.group(1))
    return None


if __name__ == '__main__':
    print("=" * 60)
    print("Starting GitHub Hackathon Tracker with LLM Integration...")
    print("=" * 60)
    print("Webhook endpoint: http://localhost:5000/webhook/github")
    print("Leaderboard API:  http://localhost:5000/leaderboard")
    print("LLM Backend:      http://localhost:8000/verify")
    print("=" * 60)
    app.run(host='0.0.0.0', port=5000, debug=True)
