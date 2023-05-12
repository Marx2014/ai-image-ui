import base64
import io
import json
import os
import sys
import time

from PIL import Image
import shutil


def print_same_line(text):
    now = time.strftime("%H:%M:%S", time.localtime())
    sys.stdout.write("\r[{}] {}".format(now, text))
    sys.stdout.flush()


def read_json(file):
    with open(file, "r", encoding='utf-8') as f:
        params = json.load(f)
    return params


def read_file(path):
    try:
        file = open(path, 'r', encoding='utf-8')
        content = file.read()
        file.close()
        return content
    except Exception as e:
        return None


def write_json(file, data):
    # 将响应保存到文件中
    with open(file, "w", encoding='utf-8') as f:
        json.dump(data, f)


def write_file(file, data):
    with open(file, "w", encoding='utf-8') as f:
        f.write(data)


def file(dirs, name):
    return os.path.join(dirs, name)


def file_move(src, dst):
    shutil.move(src, dst)


def mkdirs_if_not_exists(dirs):
    if not file_exists(dirs):
        os.makedirs(dirs)


def file_exists(dirs, name=None):
    if name is not None:
        return os.path.exists(os.path.join(dirs, name))
    else:
        return os.path.exists(dirs)


def task_finish_json_name(task_id):
    return f"{task_id}_finish.json"


def task_finish_jpg_name(task_id):
    return f"{task_id}.jpg"


def task_json_name(task_id):
    return f"{task_id}.json"


def error_log_name(task_id):
    return f"{task_id}.error_log"


def save_base64_image(base64_str, output_path):
    try:
        image_data = base64.b64decode(base64_str)
        image = Image.open(io.BytesIO(image_data))
        # 转换png为jpg,减少存储空间
        if image.format != "JPEG":
            image = image.convert("RGB")
        with open(output_path, "wb") as f:
            image.save(f, format="JPEG")
            return True
    except Exception as e:
        print("Error: ", str(e))
    return False
