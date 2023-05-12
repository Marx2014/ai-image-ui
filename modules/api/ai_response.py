import json


class AIResponse:
    def __init__(self, status: int, message: str, image_url: str = "", remain_tasks: int = 0):
        self.status = status
        self.message = message
        self.image_url = image_url
        self.remain_tasks = remain_tasks

    def to_json(self):
        return json.dumps(self.__dict__)