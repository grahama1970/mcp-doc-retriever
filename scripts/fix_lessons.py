import json
from datetime import datetime

def main():
    try:
        # Read the existing file
        with open('src/mcp_doc_retriever/docs/lessons_learned.json', 'r') as f:
            content = f.read().strip()
        
        # Handle empty or invalid JSON
        if not content:
            lessons = []
        else:
            try:
                lessons = json.loads(content)
                if not isinstance(lessons, list):
                    lessons = []
            except json.JSONDecodeError:
                lessons = []

        # Create new lesson with properly escaped strings
        new_lesson = {
            "_key": "uuid-placeholder-21",
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "role": "Boomerang Mode",
            "problem": "When defining new API request models with complex conditional validation (e.g., Pydantic models for endpoints), teams often miss key requirements or fail to enforce project module structure and verification rules.",
            "solution": "Orchestrate the implementation by: 1) Proactively researching and delivering official documentation sources for the core library (e.g., Pydantic) to the Coder, 2) Instructing the Coder to use doc_download if needed, 3) Explicitly requiring the Coder to follow project module structure rules (description, doc links, sample input/output, minimal real-world usage block), and 4) Mandating standalone verification via the __main__ block. This ensures robust, future-proof models and consistent documentation.",
            "keywords": ["orchestration", "planning", "pydantic", "conditional validation", "request model", "api", "module structure", "boomerang mode"],
            "relevant_files": ["src/mcp_doc_retriever/models.py", "src/mcp_doc_retriever/docs/lessons_learned.json"]
        }

        lessons.append(new_lesson)

        # Write back with proper formatting
        with open('src/mcp_doc_retriever/docs/lessons_learned.json', 'w') as f:
            json.dump(lessons, f, indent=2, ensure_ascii=False)
        
        print("Successfully updated lessons_learned.json")
        return 0
    except Exception as e:
        print(f"Error: {str(e)}")
        return 1

if __name__ == "__main__":
    exit(main())