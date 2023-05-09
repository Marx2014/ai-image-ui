import uuid

from fastapi import FastAPI
import os
import io
import json
import time
import threading
import requests
import uvicorn
import base64
from PIL import Image

app = FastAPI()

# taskDir = "./data/task/"
# finishDir = "./data/finish/"
# errorDir = "./data/error/"
# userImageDir = "./data/user_image/"
# baseUrl = "http://xxxxxxxxxxxxxxxxx.org"
#

taskDir = "/content/drive/MyDrive/AIImage/server/task/"
finishDir = "/content/drive/MyDrive/AIImage/server/finish/"
errorDir = "/content/drive/MyDrive/AIImage/server/error/"
userImageDir = "/content/drive/MyDrive/AIImage/server/user_image/"
baseUrl = "http://localhost:7860"


def handle_tasks():
    print("启动我的服务...")
    while True:
        try:
            time.sleep(2)
            # 找到最早提交的任务
            tasks = os.listdir(taskDir)
            if not tasks:
                print("还没有任务...")
                # 如果没有任务，则等待一段时间后再进行扫描
                continue
            task_id = min(tasks)
            task_file = os.path.join(taskDir, task_id)

            try:
                # 去掉json后缀
                task_id = task_id.split(".")[0]
                print(f"处理任务,读取json: {task_id}")
                # 读取任务数据并发送POST请求
                with open(task_file, "r", encoding='utf-8') as f:
                    params = json.load(f)
                print(f"请求AIDraw: {task_id}")

                if "init_images" in params:  # 如果是图生图+图片重绘
                    # JSON对象包含"init_images"键
                    resp = requests.post("%s/sdapi/v1/img2img" % baseUrl, json=params)
                    try:
                        # 此处用于记录用户传了什么图片,用作分析
                        init_images = params["init_images"]
                        if init_images and len(init_images) > 0:
                            save_base64_image(init_images[0], os.path.join(userImageDir, f"{task_id}_init_images.jpg"))
                        if "mask" in params:
                            save_base64_image(params["mask"], os.path.join(userImageDir, f"{task_id}_mask.jpg"))
                    except Exception as e:
                        print(f"保存图片时发生异常：{e}")
                elif "upscaling_resize" in params and "upscaler_1" in params:  # 如果是图片无损放大
                    resp = requests.post("%s/sdapi/v1/extra-single-image" % baseUrl, json=params)
                else:  # 如果是文生图
                    resp = requests.post("%s/sdapi/v1/txt2img" % baseUrl, json=params)

                if resp.status_code == 200:
                    # print(f"响应结果: {resp}")
                    resp_json = resp.json()
                    output_jpg = os.path.join(finishDir, f"{task_id}.jpg")
                    if "images" in params and save_base64_image(resp_json["images"][0], output_jpg):
                        # 去掉base64字符串替换为jpg路径, 这样对客户端不需要在json中返回那么大的图片数据还要客户端解析base64,导致麻烦
                        resp_json["images"] = f"{task_id}.jpg"
                    if "image" in params and save_base64_image(resp_json["image"], output_jpg):
                        resp_json["image"] = f"{task_id}.jpg"

                    # 将响应保存到文件中
                    with open(os.path.join(finishDir, f"{task_id}_finish.json"), "w", encoding='utf-8') as f:
                        json.dump(resp_json, f)
                    # 移动任务文件到finish目录中
                    os.rename(task_file, os.path.join(finishDir, f"{task_id}.json"))
                else:
                    # 抛出异常
                    raise Exception(f"请求失败，状态码为: {resp.status_code}")
            except Exception as e:
                print(f"请求AIDraw失败 Exception: {e}")
                # 如果请求出错，则将任务文件移动到error目录中，并记录错误日志
                os.rename(task_file, os.path.join(errorDir, f"{task_id}.json"))
                with open(os.path.join(errorDir, f"{task_id}.error_log"), "w", encoding='utf-8') as f:
                    f.write(str(e))
                continue

        except Exception as e:
            print(f"任务处理出错 Exception: {e}")


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


# 支持图生图,文生图,mask重绘,图片无损放大
@app.post("/ai_draw_commit")
async def ai_draw_commit(params: dict):
    # 生成任务ID
    task_id = f"{int(time.time())}_{uuid.uuid4().hex}"
    print(f"用户提交新任务: {task_id}")
    # 保存数据到文件中
    file_path = os.path.join(taskDir, f"{task_id}.json")
    with open(file_path, "w", encoding='utf-8') as f:
        f.write(json.dumps(params))

    remain_tasks_num = query_remain_tasks_num(task_id)

    # 输出任务相关信息
    print(f"用户提交新任务,记录json完毕: {file_path}")
    # 返回任务ID
    return {"task_id": task_id, "remain_tasks": remain_tasks_num - 1}


@app.post("/ai_draw_query")
async def ai_draw_query(params: dict):
    try:
        task_id = params['task_id']
        # 先检查error目录中是否存在任务错误文件
        if os.path.exists(os.path.join(errorDir, f"{task_id}.json")):
            content = ""
            try:
                file = open(os.path.join(errorDir, f"{task_id}.error_log"), 'r', encoding='utf-8')
                content = file.read()
                file.close()
            except Exception as e:
                pass
            return {"status": -1, "message": "图片生成失败!%s" % content}

        # 检查finish目录中是否存在任务结果文件
        if os.path.exists(os.path.join(finishDir, f"{task_id}.jpg")):
            return {"status": 0, "message": "图片生成成功!", "image_url": f"{task_id}.jpg"}

        if os.path.exists(os.path.join(taskDir, f"{task_id}.json")):
            remain_tasks_num = query_remain_tasks_num(task_id)
            return {"status": 2, "message": "图片排队中", "remain_tasks": remain_tasks_num}
    except Exception as e:
        return {"status": -3, "message": "参数异常!"}

    return {"status": -2, "message": "图片生成失败!"}


def query_remain_tasks_num(task_id):
    try:
        # 检查task目录中是否存在任务
        tasks = os.listdir(taskDir)
        tasks_num = len(tasks)
        if tasks_num == 0:
            return 0
        # 统计排在该任务前面（包括）的任务数
        before_count = 0
        for task in tasks:
            if task == f"{task_id}.json":
                break
            before_count += 1
        # 根据排在该任务前面（包括）的任务数计算剩余任务数
        remain_tasks_num = tasks_num - before_count
        return remain_tasks_num
    except Exception as e:
        return -1


def runMyServer():
    if not os.path.exists(taskDir):
        os.makedirs(taskDir)
    if not os.path.exists(finishDir):
        os.makedirs(finishDir)
    if not os.path.exists(errorDir):
        os.makedirs(errorDir)
    if not os.path.exists(userImageDir):
        os.makedirs(userImageDir)
    # 启动后台进程
    t = threading.Thread(target=handle_tasks)
    t.start()


if __name__ == "__main__":
    runMyServer()
    # 启动FastAPI应用
    uvicorn.run(app, host="127.0.0.1", port=8120)
