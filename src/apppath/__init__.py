#coding=utf-8
import pathlib

base_path = None
app_path = None
user_data_dir_path = None
main_config_file_path = None

def setup_data_path(_app_path):
    global base_path
    global app_path
    global user_data_dir_path
    global main_config_file_path

    app_path = pathlib.Path(_app_path)
    base_path = app_path.parent

    user_data_dir_path = pathlib.Path(base_path, 'StickerGenie Settings')
    user_data_dir_path.mkdir(parents=True, exist_ok=True)
    #main_config_file_path = pathlib.Path(user_data_dir_path, 'config.toml')
