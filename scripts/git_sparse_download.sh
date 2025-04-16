# sparse download of the python-arango repository
# This script will download only the files with .rst and .md extensions
git clone --filter=blob:none --no-checkout https://github.com/arangodb/python-arango.git
cd python-arango
git sparse-checkout init --cone
echo '*.rst' > .git/info/sparse-checkout
echo '*.md' >> .git/info/sparse-checkout
git checkout main