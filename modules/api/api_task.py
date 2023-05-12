import uuid
import asyncio
import qiniu
from fastapi import FastAPI, WebSocket
import threading
import requests
import uvicorn
import sys
import os

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
import datetime

from mx_file import *
# 导入 AIResponse 类
from ai_response import AIResponse

app = FastAPI()



taskDir = os.getenv('taskDir')
finishDir = os.getenv('finishDir')
errorDir = os.getenv('errorDir')
userImageDir = os.getenv('userImageDir')
baseUrl = os.getenv('baseUrl')

# 初始化七牛云对象存储
access_key = os.getenv('access_key')
secret_key = os.getenv('secret_key')
bucket_name = os.getenv('bucket_name')
bucket_url = os.getenv('bucket_url')


q = qiniu.Auth(access_key, secret_key)
bucket = qiniu.BucketManager(q)


# 使用七牛云URL签名算法对url进行处理，返回带有有效download_token的url
def sign_url(url):
    q = qiniu.Auth(access_key, secret_key)
    return q.private_download_url(url, expires=3600)


# 从七牛云获取url外链, 如果不存在, 先上传再获取外链
def fetch_image_url(img_path):
    # 获取文件名
    filename = os.path.basename(img_path)
    qiniu_path = filename

    with open(img_path, 'rb') as f:
        ret, info = bucket.stat(bucket_name, filename)
        if ret:
            print(f"已存在文件，检查是否过期:{ret['putTime']}")
            # 七牛云上已存在该文件，检查是否过期
            expires = datetime.datetime.utcnow() + datetime.timedelta(days=30)
            if ret['putTime'] / 10000000 + 3600 * 8 > expires.timestamp():
                # 未过期，直接返回七牛云的外链url
                return sign_url(f"{bucket_url}{qiniu_path}")
        print(f"开始上传")
        # 七牛云上不存在该文件或已过期，上传到七牛云并返回外链url
        token = q.upload_token(bucket_name, qiniu_path)
        ret, info = qiniu.put_data(token, qiniu_path, f)
        return sign_url(f"{bucket_url}{qiniu_path}")


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
        return tasks_num
        # if tasks_num == 0:
        #     return 0
        # # 统计排在该任务前面（包括）的任务数
        # before_count = 0
        # for task in tasks:
        #     if task == task_json_name(task_id):
        #         break
        #     before_count += 1
        # # 根据排在该任务前面（包括）的任务数计算剩余任务数
        # remain_tasks_num = tasks_num - before_count
        # return remain_tasks_num
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
    if json_obj.get("image", ""):
        return json_obj["image"]
    elif json_obj.get("images"):
        images = json_obj["images"]
        if len(images) > 0 and images[0]:
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
                    print(f"\n处理任务,读取task_id: {task_id}")
                    params = read_json(task_file)
                    # 请求AI绘图
                    resp = request_webui_task(params, task_id)
                    print(f"\n请求任务task_id完成: {task_id}:status_code={resp.status_code}")
                    if resp.status_code == 200:
                        resp_json = resp.json()

                        output_path = file(finishDir, task_finish_jpg_name(task_id))
                        print(f"\n存储图片: {output_path}")
                        if save_base64_image(get_base64_for_json(resp_json), output_path):
                            print(f"\n上传图片: {output_path}")
                            image_url = fetch_image_url(output_path)
                            resp_json["image"] = image_url
                            print(f"\n上传图片完毕: {image_url}")

                        # 将响应保存到文件中
                        # write_json(file(finishDir, task_finish_json_name(task_id)), resp_json)
                        # 移动任务文件到finish目录中
                        file_move(task_file, file(finishDir, task_json_name(task_id)))
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
        if file_exists(finishDir, task_finish_jpg_name(task_id)):
            img_file = file(finishDir, task_finish_jpg_name(task_id))
            image_url = fetch_image_url(img_file)
            response.status = 0
            response.message = f"图片生成成功!"
            response.image_url = image_url
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

    # 这里是一个优化, 如果任务还在排队, 那么每隔n秒轮询一次, 直到超时或者成功或者失败
    max_polling_count = 3 * 60
    polling_count = 0

    while polling_count < max_polling_count:
        # 判断任务是否已经完成
        response = await ai_draw_query(params)
        if response.status == 2:
            # 检测连接是否断开
            try:
                # 每隔一段时间发送一次消息，如果客户端没有响应，则抛出 WebSocketDisconnect 异常
                time_out_percent = (polling_count / max_polling_count * 100)
                await asyncio.wait_for(
                    websocket.send_json({"ping": time_out_percent, "remain_tasks": response.remain_tasks}),
                    timeout=5
                )
                await websocket.receive_text()
            except:
                # 客户端断开连接
                break

            polling_count += 1
            print(f"图片还在排队中:{task_id},序号:{response.remain_tasks}")
            await asyncio.sleep(5)
        else:
            print(f"图片完成:{task_id}:{response.message}")
            # 成功或者失败，直接响应给客户端并结束连接
            await websocket.send_json(response.to_json())
            await websocket.close()
            break

    # 如果超过最大轮询次数，返回错误信息并结束连接
    if polling_count >= max_polling_count:
        error_response = {"status": "failed", "message": "轮询超时，请稍后再试"}
        await websocket.send_json(error_response)
        await websocket.close()


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
