__version__ = "0.1.0"

# Auto-load a project-local .env so things like LISTENBRAINZ_TOKEN can
# live in /home/vin/tools/hifi/.env without needing to be exported in
# every shell. find_dotenv walks up from CWD; we also try the package
# directory so `hifi` invoked from anywhere still picks up the project
# .env. No-op when no .env exists.
try:
    from pathlib import Path
    from dotenv import load_dotenv

    load_dotenv()  # CWD-relative
    _pkg_env = Path(__file__).resolve().parents[2] / ".env"
    if _pkg_env.exists():
        load_dotenv(_pkg_env, override=False)
except ImportError:
    pass
