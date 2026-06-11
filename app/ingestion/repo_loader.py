from pathlib import Path
from urllib.parse import urlparse
from git import Repo, repo
BASE_REPO_DIR=Path("data/repos")
repo_name="psf_requests"
def get_repo_name(repo_url: str) -> str:
    path=urlparse(repo_url).path.strip("/")
    owner, repo = path.split("/")[:2]
    if repo.endswith(".git"):
        repo=repo[:-4]
    return f"{owner}_{repo}"
print(get_repo_name("https://github.com/psf/response.git"))
print(get_repo_name("https://github.com/psf/requests"))
print(get_repo_name("https://github.com/Aditya/folder.git"))

def repo_exists(repo_name: str) -> bool:
    return (BASE_REPO_DIR/repo_name).exists()
print(repo_exists("psf_requests"))

def clone_repo(repo_name: str) -> Path:
    repo_url="https://github.com/psf/requests.git"
    repo_name=get_repo_name(repo_url)
    repo_path=BASE_REPO_DIR/repo_name
    repo=Repo.clone_from(repo_url, repo_path)
    print(repo)
    print(type(repo))
    return repo_path
clone_repo(repo_name)


def update_repo(repo_path: Path) -> bool:
    try:
        repo = Repo(repo_path)
        repo.remotes.origin.pull()
        return True

    except Exception as e:
        print(f"Error updating repository: {e}")
        return False

repo_path = Path("data/repos/psf_requests")

print(update_repo(repo_path))

def prepare_repo(repo_url:str)->str:
    repo_name=get_repo_name(repo_url)
    if repo_exists(repo_name):
        update_repo(BASE_REPO_DIR/repo_name)
    else:
        clone_repo(repo_name)
    return str(BASE_REPO_DIR/repo_name)