import time
import threading
from flask import Flask
from flask import request
from flask import abort
from flask import jsonify
from common import MSG_NEW_USER
from common import MSG_UPDATE_PASSWORD
from common import USER_TASK_TOPIC
from myredis.client import redis_client
from mykafka.consumer import kafka_consumer
from mykafka.producer import kafka_producer


app = Flask(__name__)


def on_send_success(record_metadata, result):
    result['success'] = True
    result['debug_info'] = "Successfully send message to topic {}, partition {}, offset {}".\
        format(record_metadata.topic, record_metadata.partition, record_metadata.offset)


def on_send_fail(e, result):
    result['success'] = False
    result['info'] = "Fail to send message with error {}".format(e)


def register(name, department, password):
    """
    注册一个新用户
    :param name: 用户名
    :param department: 部门
    :param password: 密码
    :return: None
    """
    # 将用户信息写入redis
    key = name
    value = {
        "department": department,
        "password": password
    }
    redis_client.hset(name=key, mapping=value)

    # 将注册新用户这一事件写入kafka，任务管理系统会从kafka中读取该事件
    # kafka server应当事先建立一个名为user-task并且只包含一个partition的topic，用于传递用户管理系统和任务管理系统之间的消息
    result = {
        "success": False,
        "debug_info": ""
    }
    msg_new_user = MSG_NEW_USER.format(name=name, department=department).encode()
    kafka_producer.send(USER_TASK_TOPIC, msg_new_user).\
        add_callback(on_send_success, result=result).\
        add_errback(on_send_fail, result=result)
    time.sleep(0.1)

    return result


def login_request_is_valid(login_request):
    return ("name" in login_request.json) and ("password" in login_request.json)


@app.route('/user/api/login', methods=['POST'])
def login():
    """
    用户登录
    """
    if not login_request_is_valid(request):
        abort(400)

    # 去redis中查找该用户密码，验证用户输入的密码是否正确
    # 正确则返回True，否则返回False
    name = request.json["name"]
    password = request.json["password"]
    expected_password = redis_client.hget(name=name, key="password")
    # TODO:assert name存在的情况下password一定存在
    if expected_password is not None:
        expected_password = expected_password.decode()
    if expected_password is None:
        return "该用户不存在！"
    elif expected_password != password:
        return "密码错误！"
    else:
        return "登录成功！"


def update_password(name, old_passwd, new_passwd):
    """
    更新用户密码
    :param name: 用户名
    :param old_passwd: 旧密码
    :param new_passwd: 新密码
    :return:
    """
    # TODO:去redis中查找该用户密码，验证用户输入的旧密码是否正确
    #      如果旧密码正确，则更新用户的密码为新密码，同时将更新密码这一事件写入kafka，任务管理系统会从kafka中读取该事件
    #      验证密码的过程中，可以知道该用户是普通用户还是员工，更新密码事件需要包含这一信息，从而让任务管理系统更方便地进行查询
    #      kafka server应当事先建立一个名为user-task并且只包含一个partition的topic，用于传递用户管理系统和任务管理系统之间的消息
    #      消息格式为MSG_UPDATE_PASSWORD
    #      消息构造示例：MSG_UPDATE_PASSWORD.format(name="alice", is_employee="yes")


def consume_kafka():
    """
    从kafka中消费员工管理系统发来的消息
    :return:
    """
    while True:
        tp_to_records = kafka_consumer.poll()
        kafka_consumer.commit()
        for tp in tp_to_records:
            records = tp_to_records[tp]
            for record in records:
                msg = record.value.decode()
                msg_slices = msg.split('|')
                if msg_slices[0] == "new employee":
                    number = msg_slices[1]
                    department = msg_slices[3]
                    password = msg_slices[4]
                    register(name=number, department=department, password=password)
        time.sleep(1)


if __name__ == "__main__":
    consume_threading = threading.Thread(target=consume_kafka, name="ConsumeThreading")
    consume_threading.start()
    app.run(port=5001)
