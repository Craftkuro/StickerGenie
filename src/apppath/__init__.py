#coding=utf-8
import pathlib

base_path = None
app_path = None
user_data_dir_path = None
library_base_path = None
default_library_path = None
main_config_file_path = None

def setup_data_path(_app_path, _base_data_path=None):
    global base_path
    global app_path
    global user_data_dir_path
    global library_base_path
    global default_library_path
    global main_config_file_path

    app_path = pathlib.Path(_app_path)
    # 资源目录（app_path）和数据根目录分开：
    # 打包后 _MEIPASS 是只读资源目录（单文件是临时解包目录，单目录是 _internal），
    # 用户数据应放到独立的数据根目录，而不是资源目录或临时目录。
    base_path = (
        pathlib.Path(_base_data_path)
        if _base_data_path is not None
        else app_path.parent
    )

    user_data_dir_path = pathlib.Path(base_path, 'StickerGenie Settings')
    user_data_dir_path.mkdir(parents=True, exist_ok=True)

    library_base_path = pathlib.Path(base_path, 'StickerGenie Library')
    library_base_path.mkdir(parents=True, exist_ok=True)
    default_library_path = library_base_path / 'Default Library'
    main_config_file_path = pathlib.Path(user_data_dir_path, 'config.toml')
