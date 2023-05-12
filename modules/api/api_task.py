import uuid
from collections import defaultdict

from fastapi import FastAPI, WebSocket
import threading
import requests
import uvicorn
from typing import Dict, Set

import datetime

from mx_file import *
# 导入 AIResponse 类
from ai_response import AIResponse

app = FastAPI()

# 用一个 dict 来存储每个 task_id 对应的 WebSocket 连接集合
connected_clients: Dict[str, Set[WebSocket]] = defaultdict(set)

taskDir = "/content/drive/MyDrive/AIImage/server/task/"
finishDir = "/content/drive/MyDrive/AIImage/server/finish/"
errorDir = "/content/drive/MyDrive/AIImage/server/error/"
userImageDir = "/content/drive/MyDrive/AIImage/server/user_image/"
baseUrl = "http://localhost:7860"


def take_first_task():
    try:
        # 找到最早提交的任务
        tasks = os.listdir(taskDir)
        if not tasks:
            # 如果没有任务，则等待一段时间后再进行扫描
            return None
        task_id = min(tasks)
        task_file = file(taskDir, task_id)
        # 去掉json后缀
        task_id = task_id.split(".")[0]
        return task_id, task_file
    except:
        return None


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
            if task == task_json_name(task_id):
                break
            before_count += 1
        # 根据排在该任务前面（包括）的任务数计算剩余任务数
        remain_tasks_num = tasks_num - before_count
        return remain_tasks_num
    except Exception as e:
        return -1


def request_webui_task(params, task_id):
    if "init_images" in params:  # 如果是图生图+图片重绘
        # JSON对象包含"init_images"键
        resp = requests.post("%s/sdapi/v1/img2img" % baseUrl, json=params)
        try:
            # 此处用于记录用户传了什么图片,用作分析
            init_images = params["init_images"]
            if init_images and len(init_images) > 0:
                save_base64_image(init_images[0], file(userImageDir, f"{task_id}_init_images.jpg"))
            if "mask" in params:
                save_base64_image(params["mask"], file(userImageDir, f"{task_id}_mask.jpg"))
        except Exception as e:
            print(f"保存图片时发生异常：{e}")
    elif "upscaling_resize" in params and "upscaler_1" in params:  # 如果是图片无损放大
        resp = requests.post("%s/sdapi/v1/extra-single-image" % baseUrl, json=params)
    else:  # 如果是文生图
        resp = requests.post("%s/sdapi/v1/txt2img" % baseUrl, json=params)
    return resp


def get_base64_for_json(json_obj):
    if json_obj.get("image", "").startswith("data:image/"):
        return json_obj["image"]
    elif json_obj.get("images"):
        images = json_obj["images"]
        if len(images) > 0 and images[0].startswith("data:image/"):
            return images[0]
    return None


def handle_tasks():
    count = 0
    while True:
        try:
            time.sleep(2)
            task = take_first_task()
            if task is None:
                print_same_line(f"还没有任务...{count}")
                count += 1
                continue
            else:
                task_id, task_file = task
                try:
                    print(f"处理任务,读取task_id: {task_id}")
                    params = read_json(task_file)
                    # 请求AI绘图
                    resp = request_webui_task(params, task_id)
                    if resp.status_code == 200:
                        resp_json = resp.json()

                        if save_base64_image(get_base64_for_json(resp_json), task_finish_jpg_name(task_id)):
                            resp_json["image"] = task_finish_jpg_name(task_id)

                        # 将响应保存到文件中
                        # write_json(file(finishDir, task_finish_json_name(task_id)), resp_json)
                        # 移动任务文件到finish目录中
                        file_move(task_file, file(finishDir, task_json_name(task_id)))
                        # 通知客户端更新
                        ws_response_to_client(task_id)
                    else:
                        # 未知异常
                        raise Exception(f"请求失败，状态码为: {resp.status_code}")
                except Exception as e:
                    print(f"请求AIDraw失败 Exception: {e}")
                    # 如果请求出错，则将任务文件移动到error目录中，并记录错误日志
                    file_move(task_file, file(errorDir, task_json_name(task_id)))
                    write_file(file(errorDir, error_log_name(task_id)), str(e))
        except Exception as e:
            print(f"任务处理出错 Exception: {e}")


async def ws_response_to_client(task_id):
    # 如果有客户端正在连接websocket，则立即通知客户端
    if task_id in connected_clients:
        for client in connected_clients[task_id]:
            print(f"响应客户端: task_id={task_id} {client.client.host}:{client.client.port}")
            # 查询一次AI结果
            response = await ai_draw_query({"task_id": task_id})
            await client.send_json(response.to_json())
            print(f"响应客户端: task_id={task_id} 完毕,response={response}")
            await client.close()  # 关闭客户端连接

        del connected_clients[task_id]


# 支持图生图,文生图,mask重绘,图片无损放大
async def api_ai_draw_commit(params: dict):
    # 生成任务ID
    task_id = f"{int(time.time())}_{uuid.uuid4().hex}"
    print(f"用户提交新任务: {task_id}")
    # 保存数据到文件中
    file_path = file(taskDir, task_json_name(task_id))
    write_json(file_path, params)
    # 查询排队情况
    remain_tasks_num = query_remain_tasks_num(task_id)
    # 返回任务ID
    return {"task_id": task_id, "remain_tasks": remain_tasks_num - 1}


async def api_ai_draw_query(params: dict):
    response = await ai_draw_query(params)
    return response.to_json()


async def ai_draw_query(params: dict):
    response = AIResponse(status=-2, message="图片生成失败!")
    try:
        task_id = params['task_id']
        # 先检查error目录中是否存在任务错误文件
        task_id_json = task_json_name(task_id)

        # 检查finish目录中是否存在任务结果文件
        if file_exists(finishDir, task_finish_json_name(task_id)):
            resp_json = read_json(file(finishDir, task_finish_json_name(task_id)))
            base64_img = ""
            if "images" in resp_json:
                try:
                    base64_img = resp_json["images"][0]
                except:
                    pass
            if "image" in resp_json:
                try:
                    base64_img = resp_json["image"]
                except:
                    pass
            if base64_img and base64_img.strip():
                response.status = 0
                response.message = f"图片生成成功!"
                response.image_base64 = base64_img
        elif file_exists(errorDir, task_id_json):
            content = read_file(file(errorDir, error_log_name(task_id)))
            response.status = -1
            response.message = f"图片生成失败!{content}"
        elif file_exists(taskDir, task_id_json):
            remain_tasks_num = query_remain_tasks_num(task_id)
            response.status = 2
            response.message = f"图片排队中..."
            response.remain_tasks = remain_tasks_num
    except Exception as e:
        response.status = -3
        response.message = "参数异常!"

    return response


async def api_websocket_handler(websocket: WebSocket):
    await websocket.accept()
    # 接收任务 ID
    params = json.loads(await websocket.receive_text())
    task_id = params['task_id']
    # 判断任务是否已经完成
    response = await ai_draw_query(params)
    if response.status == 0 and not response.image_base64:
        # 如果图片已经生成，直接发送图片
        await websocket.send_json(response.to_json())
        await websocket.close()  # 关闭客户端连接

    elif response.status == 2:
        # 如果图片还在排队中, 那么记录task_id client
        connected_clients[task_id].add(websocket)
    else:
        # 其它情况视为失败,直接告诉客户端已经失败
        await websocket.send_json(response.to_json())
        await websocket.close()  # 关闭客户端连接


def runMyServer(router):
    mkdirs_if_not_exists(taskDir)
    mkdirs_if_not_exists(finishDir)
    mkdirs_if_not_exists(errorDir)
    mkdirs_if_not_exists(userImageDir)

    router.add_api_route(methods=["POST"], path="/ai_draw_commit", endpoint=api_ai_draw_commit)
    router.add_api_route(methods=["POST"], path="/ai_draw_query", endpoint=api_ai_draw_query)
    # 添加 Websocket 路由
    router.add_websocket_route(path="/ai_draw_query_ws", endpoint=api_websocket_handler)
    # 启动后台进程
    t = threading.Thread(target=handle_tasks)
    t.start()


if __name__ == "__main__":
    from fastapi import APIRouter

    router = APIRouter()
    app.router = router
    runMyServer(router)
    # 启动FastAPI应用
    uvicorn.run(app, host="127.0.0.1", port=8120)
