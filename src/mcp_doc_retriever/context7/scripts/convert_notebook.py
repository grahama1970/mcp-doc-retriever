import json
import nbformat

# 1) Load the JSON (must follow the notebook schema)
with open(
    "/Users/robert/Documents/dev/workspace/experiments/mcp-doc-retriver/src/mcp_doc_retriever/context7/g.json",
    "r",
    encoding="utf-8",
) as f:
    notebook_dict = json.load(f)

# 2) Convert dict → NotebookNode (validates schema)
nb = nbformat.from_dict(notebook_dict)

# 3) Write out the .ipynb
with open("output.ipynb", "w", encoding="utf-8") as f:
    nbformat.write(nb, f)

print("✅ Wrote output.ipynb")
