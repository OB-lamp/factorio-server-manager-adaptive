import os


os.environ["FACTORIO_SERVER_MANAGER_EDITION"] = "stable-2.1.23"

from factorio_server_manager.app import main


if __name__ == "__main__":
    main()
