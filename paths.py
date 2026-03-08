import os


def get_app_data_dir():
    """返回应用数据根目录：项目目录下的 data/ 文件夹，方便打包分发。"""
    base = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
    os.makedirs(base, exist_ok=True)
    return base


def get_db_path():
    return os.path.join(get_app_data_dir(), "skimclass.db")


def get_faiss_dir(course_id):
    d = os.path.join(get_app_data_dir(), "faiss_indexes", str(course_id))
    os.makedirs(d, exist_ok=True)
    return d


def get_recordings_dir():
    d = os.path.join(get_app_data_dir(), "recordings")
    os.makedirs(d, exist_ok=True)
    return d


def get_exports_dir():
    d = os.path.join(get_app_data_dir(), "exports")
    os.makedirs(d, exist_ok=True)
    return d
