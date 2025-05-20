import re
import os
import yaml
import asyncio

import dotenv
from openai import OpenAI
from browser_automation import BrowserAutomation
from pocketflow import AsyncNode, AsyncFlow


# 加载环境变量
dotenv.load_dotenv()

def call_llm(prompt):
    client = OpenAI(api_key=os.getenv("LLM_API_KEY"), base_url=os.getenv("LLM_BASE_URL"))
    response = client.chat.completions.create(
        model=os.getenv("LLM_MODEL"),
        messages=[{"role": "user", "content": prompt}]
    )
    return response.choices[0].message.content

def clean_xpath(xpath: str) -> str:
        """清理XPath表达式中的多余转义字符
        
        Args:
            xpath (str): 原始XPath表达式
            
        Returns:
            str: 清理后的XPath表达式
        """
        # 移除多余的转义字符
        cleaned = xpath.replace("\\'", "'")
        # 如果不是以xpath://开头，添加前缀
        if not cleaned.startswith("xpath://"):
            cleaned = f"xpath://{cleaned.lstrip('/')}"
        return cleaned

def filter_interactive_elements(elements):
    """
    过滤interactive_elements列表，只保留tag的值并去除index信息。
    使用正则表达式从字符串中提取标签内容。
    
    Args:
        elements: 包含元素信息的列表，每个元素是一个字符串，格式如：'{''index'': 0, ''tag'': ''<img alt="close"></img>''}'。
        
    Returns:
        过滤后的列表，只包含tag的值，如：'<a>首页</a>'。
    """
    filtered_elements = []
    
    # 正则表达式模式，用于匹配tag字段中的值
    # 匹配三种引号格式: ''<tag>...</tag>'', '<tag>...</tag>', "<tag>...</tag>"
    pattern = r"'tag':\s*(?:''(.*?)''|'(.*?)'|\"(.*?)\")"
    
    for element in elements:
        # 使用正则表达式提取tag值
        match = re.search(pattern, element)
        if match:
            # 获取匹配到的组，只有一个组会有值，其他为None
            groups = match.groups()
            # 找到非None的值
            tag_value = next((g for g in groups if g is not None), None)
            if tag_value:
                filtered_elements.append(tag_value)
    
    return filtered_elements
    
# 延迟初始化 global browser 变量
browser = None

def safe_env(env):
    """
    将环境数据转换为仅包含安全可序列化数据的字典，
    例如将对象转换为字符串描述。
    """
    safe = {}
    if "open_tabs" in env:
        try:
            safe["open_tabs"] = [str(tab) for tab in env["open_tabs"]]
        except Exception as e:
            safe["open_tabs"] = f"Error converting open_tabs: {e}"
    if "current_tab" in env:
        try:
            safe["current_tab"] = str(env["current_tab"])
        except Exception as e:
            safe["current_tab"] = f"Error converting current_tab: {e}"
    if "interactive_elements" in env:
        try:
            # 先将元素转换为字符串列表
            elements_str = [str(el) for el in env["interactive_elements"]]
            # 使用filter_interactive_elements函数过滤，只保留tag值
            safe["interactive_elements"] = filter_interactive_elements(elements_str)
        except Exception as e:
            safe["interactive_elements"] = f"Error converting interactive_elements: {e}"
    return safe

# def summarize_operation_history(operation_history):
#     """
#     分析操作历史并生成简洁的摘要，包括操作统计、类型统计、最近操作和错误信息。
    
#     Args:
#         operation_history: 操作历史记录列表，每个记录包含'operation'和'result'字段。
        
#     Returns:
#         包含摘要信息的字典，包括总操作数、成功/失败次数、操作类型统计、最近操作和最近错误。
#     """
#     if not operation_history:
#         return {}

    
#     return summary


def safe_data(data: dict):
    """
    将综合数据转换为安全可序列化的字典。
    包含 'env', 'last_op', 'op_result', 'task', 'operation_history' 五个部分。
    """
    safe = {}
    if "env" in data:
        safe["env"] = safe_env(data["env"])
    if "last_op" in data:
        safe["last_op"] = data["last_op"] if isinstance(data["last_op"], dict) else str(data["last_op"])
    if "op_result" in data:
        safe["op_result"] = str(data["op_result"]) if data["op_result"] is not None else "None"
    if "task" in data:
        safe["task"] = str(data["task"])
    if data["operation_history"] is not None and "operation_history" in data:
        # 将操作历史转换为安全可序列化的格式
        safe_history = []
        for record in data["operation_history"]:
            safe_record = {
                "operation": record["operation"] if isinstance(record["operation"], dict) else str(record["operation"]),
                "result": str(record["result"]) if record["result"] is not None else "None"
            }
            safe_history.append(safe_record)
        
        # 添加操作历史摘要
        # safe["operation_history_summary"] = summarize_operation_history(data["operation_history"])
        
        # 根据需要决定是否保留完整历史记录
        # 如果历史记录很长，可以只保留最近的几条
        if len(safe_history) > 5:
            safe["operation_history"] = safe_history[-5:]
        else:
            safe["operation_history"] = safe_history
    return safe

# 异步节点：获取当前浏览器环境
class AsyncGetBrowserEnv(AsyncNode):
    async def prep_async(self, shared):
        return None

    async def exec_async(self, prep_res):
        open_tabs = await asyncio.to_thread(browser.list_tabs)
        current_tab = await asyncio.to_thread(lambda: browser.page.latest_tab.title)
        interactive_elements = await asyncio.to_thread(browser.get_clickable_elements)
        # 等待1秒后清除高亮
        await asyncio.sleep(1)
        await asyncio.to_thread(browser.remove_highlight)
        
        env = {
            "open_tabs": open_tabs,
            "current_tab": current_tab,
            "interactive_elements": interactive_elements
        }
        return env

    async def post_async(self, shared, prep_res, exec_res):
        # 存储浏览器环境信息
        shared["browser_env"] = exec_res
        return "default"

# 异步节点：规划下一步操作
class AsyncPlanOperation(AsyncNode):
    async def prep_async(self, shared):
        if "task" not in shared or not shared["task"]:
            raise Exception("任务信息缺失，请提供有效的 task")
        task = shared["task"]

        env = shared.get("browser_env", {})
        last_op = shared.get("planned_operation", {})
        op_result = shared.get("operation_result", None)
        
        operation_history = shared.get("operation_history", [])
        # operation_history = None # TODO 暂时禁用
        # 获取上一次失败的操作信息，用于错误恢复
        last_failed_operation = shared.get("last_failed_operation", None)
        retry_count = shared.get("retry_count", 0)
        
        data = {
            "env": env,
            "last_op": last_op,
            "op_result": op_result,
            "task": task,
            "operation_history": operation_history,
            "last_failed_operation": last_failed_operation,
            "retry_count": retry_count,
            "available_actions": [
                "click_element", "click_to_upload", "input_text", "send_keys", "scroll_down",
                "scroll_up", "go_to_url", "go_back", "open_tab", "close_tab", "switch_tab"
            ]
        }
        return data
    async def exec_async(self, data):
        safe = safe_data(data)
        yaml_info = yaml.dump(safe, allow_unicode=True)
        prompt = "你是一个浏览器自动化助手，当前任务为：" + str(safe.get('task')) + "。\n"
        
        # 只有在last_op存在且不为空时才添加到提示词中
        if safe.get('last_op') and safe.get('last_op') != '无':
            prompt += "上一步操作：" + str(safe.get('last_op')) + "\n"
            
        # 处理操作结果，特别是错误情况
        op_result = safe.get('op_result')
        if op_result and op_result != '无' and op_result != 'None':
            # 检查是否是错误结果
            if isinstance(data.get('op_result'), dict) and data.get('op_result').get('error') is True:
                error_info = data.get('op_result')
                error_type = error_info.get('error_type', '未知错误')
                error_message = error_info.get('message', '未提供错误详情')
                prompt += f"上一步操作失败！错误类型：{error_type}，错误信息：{error_message}\n"
                
                # 添加错误恢复建议
                if error_type == 'parameter_validation':
                    prompt += "请检查参数是否正确，确保提供所有必要的参数。\n"
                elif error_type in ['ConnectionError', 'TimeoutError', 'NetworkError']:
                    prompt += "这可能是网络问题，请考虑重试或使用不同的方法。\n"
                elif 'NotFound' in error_type or 'ElementNotFound' in error_type:
                    prompt += "元素未找到，请考虑使用更精确的定位方式或先滚动页面。\n"
                
                # 如果有上一次失败的操作，添加到提示中
                last_failed_op = data.get('last_failed_operation')
                if last_failed_op:
                    prompt += f"失败的操作详情：{str(last_failed_op)}\n"
                    
                # 添加重试次数信息
                retry_count = data.get('retry_count', 0)
                if retry_count > 0:
                    prompt += f"这是第{retry_count}次尝试解决此问题。\n"
            else:
                prompt += "操作结果：" + str(op_result) + "\n"
                
        # 添加操作历史摘要而不是完整历史记录
        # if 'operation_history_summary' in safe:
        #     summary = safe['operation_history_summary']
        #     prompt += f"操作历史摘要:\n"
            # prompt += f"- 总操作数: {summary['total_operations']}\n"
            # prompt += f"- 成功操作: {summary['successful_operations']}\n"
            # prompt += f"- 失败操作: {summary['failed_operations']}\n"
            
            # 添加操作类型统计
            # if summary['operation_types']:
            #     prompt += "- 操作类型统计:\n"
            #     for op_type, count in summary['operation_types'].items():
            #         prompt += f"  - {op_type}: {count}\n"
            
            # 添加最近的操作
            # if summary['recent_operations']:
            #     prompt += "- 最近的操作:\n"
            #     for op in summary['recent_operations']:
            #         if 'action' in op:
            #             prompt += f"  - {op['action']}: {op['params']}\n"
            #         else:
            #             prompt += f"  - {op['description']}\n"
            
            # # 添加最近的错误
            # if summary['recent_errors']:
            #     prompt += "- 最近的错误:\n"
            #     for err in summary['recent_errors']:
            #         prompt += f"  - {err['operation']}: {err['error_type']} - {err['message']}\n"
            
        # 添加浏览器环境信息
        # TODO: 细化可交互控件信息，如可点击、普通输入框、文件上传框
        # - click_to_upload(xpath, file_paths) # 自然的文件上传方式，无需在 DOM 里找控件，只要自然地点击触发文件选择框
        prompt += "浏览器环境信息：\n" + yaml_info + """
available_actions: 
    - click_element(xpath)
    - input_text(xpath,text) # 输入文本, 也可以用于上传文件，如：//input[@type='file'] 或者 //input[@accept='image/png, image/jpeg']
    - click_to_upload(xpath, file_paths) # 自然的文件上传方式，无需在 DOM 里找控件，只要自然地点击触发文件选择框，优先使用input_text上传文件
    - send_keys(xpath, keys)
    - scroll_down(pixel: int=300)
    - scroll_up(pixel: int=300)
    - go_to_url(url) # 在当前标签页中打开新网址
    - open_tab(url) # 打开新标签页
    - go_back()
    - close_tab(title: Optional[str]=None, url: Optional[str]=None)
    - switch_tab(title: Optional[str]=None, url: Optional[str]=None)

B站上传视频SOP: input_text("//input[@type='file']", path to vedio)
B站上传封面SOP: 
1. 点击更改封面
action: click_element
params:
    xpath: //span[text()='更改封面']

1. 点击上传封面
action: click_element
params:
    xpath: //div[text()='上传封面']
2. 点击并上传封面图片
action: click_to_upload
params:
  xpath: //div[text()="拖拽图片到此或点击上传"]
  file_paths: path to img
3. 点击完成
action: click_element
params:
    xpath: //span[text()=" 完成 "]


请根据上述信息生成下一步的操作计划，并直接输出符合以下 YAML 格式的计划：
```yaml
action: 操作类型
params:
  xpath: 元素路径  # 必须根据interactive_elements编写, 优先使用等号而不是包含, 当action为click_element/input_text/send_keys时必填
  text: 输入文本  # 当action为input_text时必填
  keys: 按键序列  # 当action为send_keys时必填
  pixel: 滚动像素  # 当action为scroll_down/scroll_up时可选，默认300
  url: 目标网址  # 当action为go_to_url/open_tab时必填
  tab: 标签页标识  # 当action为switch_tab时可选
  file_paths: 文件路径 # 当action为click_to_upload时必填
```
请确保输出的内容仅包含 YAML 格式的数据，不要包含其他额外文本。"""
        print("Prompt:\n", prompt)
        
        # 添加重试机制，防止LLM调用失败
        max_retries = 3
        for attempt in range(max_retries):
            try:
                plan_yaml = call_llm(prompt)
                if "```yaml" in plan_yaml:
                    plan_yaml = plan_yaml.split("```yaml")[1].split("```")[0].strip()
                parsed_plan = yaml.safe_load(plan_yaml)
                print(type(parsed_plan))
                print("Plan:\n", plan_yaml)
                if not isinstance(parsed_plan, dict) or "action" not in parsed_plan:
                    if attempt < max_retries - 1:
                        print(f"操作计划格式无效，尝试重新生成 (尝试 {attempt+1}/{max_retries})")
                        continue
                    else:
                        raise ValueError("生成的操作计划格式无效")

                # 判断参数中是否包含xpath
                if "xpath" in parsed_plan.get("params", {}):
                    # 验证xpath是否合法
                    ele_xpath = parsed_plan["params"]["xpath"]
                    print(f"xpath: {ele_xpath}")
                    eles = browser.page.latest_tab.eles(clean_xpath(ele_xpath))
                    print(f"eles: {eles}")
                    if len(eles) == 0:
                        print("没有查找到任何元素")
                        # 没有查找到任何元素，尝试将等号改成包含关系并重新查找
                        modified_xpath = ele_xpath.replace('=', 'contains(., ')
                        modified_xpath = modified_xpath.replace(']', ')]')
                        print(f"尝试修改后的xpath: {modified_xpath}")
                        eles = browser.page.latest_tab.eles(clean_xpath(modified_xpath))
                        if len(eles) == 1:
                            print("使用包含关系查找成功")
                            parsed_plan["params"]["xpath"] = modified_xpath
#                         else:
#                             print(f"使用包含关系查找后找到 {len(eles)} 个元素，重新规划")
#                             # 准备数据供LLM重新规划
#                             message = f"没有找到匹配元素 '{ele_xpath}'，请使用更精确的xpath重新规划"
#                             prompt = f"""前一个计划失败: {yaml.dump(parsed_plan)}
# 失败原因: {message}
# 请重新规划下一步操作。
# 请使用相同的YAML格式输出新的操作计划。"""
#                             try:
#                                 new_plan_yaml = call_llm(prompt)
#                                 parsed_plan = yaml.safe_load(new_plan_yaml)
#                                 continue
#                             except Exception as e:
#                                 print(f"调用LLM重新规划失败: {e}")
#                                 continue
#                     elif len(eles) > 1:
#                         print("查找到多个元素")
#                         # 查找到多个元素，将所有元素信息传回llm让其重新规划
#                         elements_html = []
#                         for i, ele in enumerate(eles):
#                             print(ele.html)
#                             elements_html.append(f"元素{i+1}: {ele.html}")
                        
#                         # 准备数据供LLM重新规划
#                         elements_info = "\n".join(elements_html)
#                         prompt = f"""前一个计划: {yaml.dump(parsed_plan)}
# 发现多个匹配元素，请根据下面的元素信息，选择最合适的元素或提供更精确的xpath:
# {elements_info}
# 请使用相同的YAML格式输出新的操作计划。"""
#                         try:
#                             new_plan_yaml = call_llm(prompt)
#                             parsed_plan = yaml.safe_load(new_plan_yaml)
#                             continue
#                         except Exception as e:
#                             print(f"调用LLM重新规划失败: {e}")
#                             continue
                return parsed_plan
            except Exception as e:
                if attempt < max_retries - 1:
                    print(f"生成操作计划时出错: {str(e)}，尝试重新生成 (尝试 {attempt+1}/{max_retries})")
                    await asyncio.sleep(1)  # 简单的退避策略
                else:
                    print(f"多次尝试生成操作计划失败: {str(e)}，使用默认计划")
                    return {
                        "action": "scroll_down",
                        "params": {"pixel": 300}
                    }
        
        # 这里不应该被执行到，但为了安全起见
        return {
            "action": "scroll_down",
            "params": {"pixel": 300}
        }
    async def post_async(self, shared, prep_res, exec_res):
        # 存储计划操作
        shared["planned_operation"] = exec_res
        return "default"

# 异步节点：执行操作，支持所有可用动作
class AsyncExecuteOperation(AsyncNode):

    # 操作映射表，将操作名称映射到对应的方法和参数
    _operation_map = {
        "click_element": {
            "method": "click_element",
            "required_params": ["xpath"],
            "optional_params": [],
            "args_map": lambda params: [params.get("xpath")]
        },
        "click_to_upload": {
            "method": "click_to_upload",
            "required_params": ["xpath", "file_paths"],
            "optional_params": [],
            "args_map": lambda params: [params.get("xpath"), params.get("file_paths")]
        },
        "input_text": {
            "method": "input_text",
            "required_params": ["xpath", "text"],
            "optional_params": [],
            "args_map": lambda params: [params.get("xpath"), params.get("text")]
        },
        "send_keys": {
            "method": "send_keys",
            "required_params": ["xpath", "keys"],
            "optional_params": [],
            "args_map": lambda params: [params.get("xpath"), params.get("keys")]
        },
        "scroll_down": {
            "method": "scroll_down",
            "required_params": [],
            "optional_params": ["pixel"],
            "args_map": lambda params: [params.get("pixel", 300)]
        },
        "scroll_up": {
            "method": "scroll_up",
            "required_params": [],
            "optional_params": ["pixel"],
            "args_map": lambda params: [params.get("pixel", 300)]
        },
        "go_to_url": {
            "method": "go_to_url",
            "required_params": ["url"],
            "optional_params": [],
            "args_map": lambda params: [params.get("url")]
        },
        "go_back": {
            "method": "go_back",
            "required_params": [],
            "optional_params": [],
            "args_map": lambda params: []
        },
        "open_tab": {
            "method": "open_tab",
            "required_params": ["url"],
            "optional_params": [],
            "args_map": lambda params: [params.get("url")]
        },
        "close_tab": {
            "method": "close_tab",
            "required_params": [],
            "optional_params": [],
            "args_map": lambda params: []
        },
        "switch_tab": {
            "method": "switch_tab",
            "required_params": [],
            "optional_params": ["title", "url"],
            "args_map": lambda params: [params.get("title", None), params.get("url", None)]
        }
    }
    

    async def prep_async(self, shared):
        op = shared.get("planned_operation")
        if op is None or not isinstance(op, dict):
            raise Exception("执行节点未收到有效的操作计划")
        # 添加重试计数器到shared中
        if "retry_count" not in shared:
            shared["retry_count"] = 0
        # 添加上一次失败的操作记录
        if "last_failed_operation" not in shared:
            shared["last_failed_operation"] = None
        return op

    
    async def exec_async(self, op):
        action = op.get("action")
        params = op.get("params", {})
        
        # 统一参数处理
        if isinstance(params, str):
            params = {"xpath": params} if action in ["click_element", "click_to_upload", "input_text", "send_keys"] else {}
        
        # 检查操作是否存在于映射表中
        if action not in self._operation_map:
            return {
                "error": True, 
                "error_type": "unknown_action",
                "message": f"未知的操作类型: {action}"
            }
        
        # 获取操作配置
        op_config = self._operation_map[action]
        
        # 参数验证
        try:
            self._validate_params_by_config(action, params, op_config)
        except Exception as e:
            return {
                "error": True, 
                "error_type": "parameter_validation",
                "message": f"参数验证失败: {str(e)}"
            }
            
        try:
            # 获取方法名和参数
            method_name = op_config["method"]
            args = op_config["args_map"](params)
            
            # 获取浏览器对象的方法
            browser_method = getattr(browser, method_name)
            
            # 执行方法
            result = await asyncio.to_thread(browser_method, *args)
            return result
        except Exception as e:
            # 捕获异常但不抛出，而是返回详细的错误信息
            error_type = type(e).__name__
            error_message = f"执行操作{action}失败: {str(e)}"
            print(error_message)
            return {
                "error": True, 
                "error_type": error_type,
                "action": action,
                "params": params,
                "message": error_message
            }

    def _validate_params(self, action, params):
        """验证操作参数的有效性（旧方法，保留用于兼容）"""
        if action in ["click_element", "input_text", "send_keys"] and "xpath" not in params:
            raise ValueError(f"{action}操作必须提供xpath参数")
        if action == "input_text" and "text" not in params:
            raise ValueError("input_text操作必须提供text参数")
        if action == "send_keys" and "keys" not in params:
            raise ValueError("send_keys操作必须提供keys参数")
        if action in ["go_to_url", "open_tab"] and "url" not in params:
            raise ValueError(f"{action}操作必须提供url参数")
            
    def _validate_params_by_config(self, action, params, op_config):
        """根据操作配置验证参数的有效性"""
        # 检查必需参数
        for param in op_config["required_params"]:
            if param not in params:
                raise ValueError(f"{action}操作必须提供{param}参数")
        
        # 可以在这里添加更多的验证逻辑，例如参数类型检查等

    async def post_async(self, shared, prep_res, exec_res):
        shared["operation_result"] = exec_res
        
        # 检查操作是否出错
        is_error = isinstance(exec_res, dict) and exec_res.get("error") is True
        
        # 处理错误情况
        if is_error:
            # 记录失败的操作
            shared["last_failed_operation"] = prep_res
            # 增加重试计数
            shared["retry_count"] = shared.get("retry_count", 0) + 1
            
            # 如果是网络或临时性错误且重试次数未超过最大值，则返回重试路径
            error_type = exec_res.get("error_type", "")
            if error_type in ["ConnectionError", "TimeoutError", "NetworkError"] and shared["retry_count"] <= 3:
                print(f"临时性错误，准备重试 ({shared['retry_count']}/3)...")
                # 延迟一段时间后重试
                await asyncio.sleep(1)  # 简单的退避策略
                return "retry"
            else:
                # 其他错误或重试次数过多，返回错误路径
                return "error"
        else:
            # 操作成功，重置重试计数和失败操作记录
            shared["retry_count"] = 0
            shared["last_failed_operation"] = None
        
        # 将操作及结果添加到历史记录中
        if "operation_history" in shared:
            # 确保操作记录格式一致，处理params可能是字符串的情况
            if isinstance(prep_res, dict) and isinstance(prep_res.get("params"), str):
                # 如果params是字符串，转换为字典格式
                action = prep_res.get("action")
                params = prep_res.get("params", "")
                if action in ["click_element", "input_text", "send_keys"]:
                    prep_res["params"] = {"xpath": params}
                
            operation_record = {
                "operation": prep_res,
                "result": exec_res
            }
            shared["operation_history"].append(operation_record)

        return "success"

# 异步节点：观察操作结果并生成更新后的环境信息（使用 call_llm 分析，YAML 格式输出）
class AsyncObserveResult(AsyncNode):
    async def prep_async(self, shared):
        # 处理迭代计数和获取任务历史
        iteration = shared.get("iteration", 0) + 1
        shared["iteration"] = iteration
        
        # 获取任务和操作历史
        task = shared.get("task", "")
        operation_history = shared.get("operation_history", [])
        browser_env = shared.get("browser_env", {})
        
        return {
            "task": task,
            "operation_history": operation_history,
            "browser_env": browser_env,
            "iteration": iteration
        }
        
    async def exec_async(self, prep_res):
        # 从prep_async获取准备好的数据
        task = prep_res["task"]
        operation_history = prep_res["operation_history"]
        browser_env = prep_res["browser_env"]
        iteration = prep_res["iteration"]
        
        # 如果有足够的操作历史，使用LLM判断任务是否完成
        completion_result = None
        if operation_history and len(operation_history) > 0:
            # 准备用于LLM分析的数据
            safe_history = []
            for record in operation_history:
                safe_record = {
                    "operation": record["operation"] if isinstance(record["operation"], dict) else str(record["operation"]),
                    "result": str(record["result"]) if record["result"] is not None else "None"
                }
                safe_history.append(safe_record)
            
            # 构建提示词
            history_yaml = yaml.dump(safe_history, allow_unicode=True)
            completion_prompt = f"""你是一个浏览器自动化助手，当前任务为：{task}。
根据以下操作历史和当前浏览器环境，判断任务是否已经完成：

操作历史：
{history_yaml}

当前浏览器环境：
{yaml.dump(safe_env(browser_env), allow_unicode=True)}

请直接回答"是"或"否"，表示任务是否已经完成。"""
            
            # 调用LLM判断任务是否完成
            completion_result = call_llm(completion_prompt)
            print("任务完成判断结果:", completion_result)
        
        return {
            "completion_result": completion_result,
            "iteration": iteration
        }
        
    async def post_async(self, shared, prep_res, exec_res):
        # 从exec_async获取结果，添加空值检查
        if exec_res is None:
            # 如果exec_res为None，检查迭代次数决定是否结束
            iteration = shared.get("iteration")
            if iteration is not None and iteration >= 30:  # 设置一个较大的最大迭代次数作为兜底
                shared["final_message"] = "达到最大迭代次数，任务结束"
                return "finish"
            return "continue"  # 默认继续执行
            
        completion_result = exec_res.get("completion_result")
        iteration = exec_res.get("iteration")
        
        # 根据LLM的回答决定是否结束流程
        if completion_result and ("是" in completion_result.lower() or "完成" in completion_result.lower()):
            # 存储任务完成的消息
            shared["final_message"] = f"任务已完成：{completion_result}"
            return "finish"
        
        # 如果LLM判断任务未完成，或者无法判断，则根据迭代次数决定
        if iteration is not None and iteration >= 30:  # 设置一个较大的最大迭代次数作为兜底
            shared["final_message"] = "达到最大迭代次数，任务结束"
            return "finish"
        else:
            # 任务未完成，继续执行
            return "continue"

# 异步节点：完成任务
class AsyncFinishNode(AsyncNode):
    async def prep_async(self, shared):
        # 获取任务和操作历史，用于生成更详细的总结
        task = shared.get("task", "")
        operation_history = shared.get("operation_history", [])
        return {
            "task": task,
            "operation_history": operation_history
        }
        
    async def exec_async(self, prep_res):
        # 从prep_async获取准备好的数据
        task = prep_res.get("task", "")
        operation_history = prep_res.get("operation_history", [])
        
        # 如果有操作历史，生成一个更详细的总结
        if operation_history and len(operation_history) > 0:
            # 准备用于LLM分析的数据
            safe_history = []
            for record in operation_history:
                safe_record = {
                    "operation": record["operation"] if isinstance(record["operation"], dict) else str(record["operation"]),
                    "result": str(record["result"]) if record["result"] is not None else "None"
                }
                safe_history.append(safe_record)
            
            # 构建提示词
            history_yaml = yaml.dump(safe_history[-5:], allow_unicode=True)  # 只使用最后5条记录
            summary_prompt = f"""你是一个浏览器自动化助手，当前任务为：{task}。
根据以下操作历史，生成一个简洁的总结，描述任务完成情况：

操作历史：
{history_yaml}

请生成一个简洁的总结，描述任务完成情况。"""
            
            # 调用LLM生成总结
            try:
                summary = call_llm(summary_prompt)
                return summary
            except Exception as e:
                print(f"生成总结时出错: {e}")
                return "任务已完成，但无法生成详细总结"
        
        return "任务已完成"
        
    async def post_async(self, shared, prep_res, exec_res):
        # 存储最终消息，添加空值检查
        if exec_res is not None:
            shared["final_message"] = exec_res
        else:
            shared["final_message"] = "任务已完成"
        return None

# 自定义 AsyncFlow 子类用于返回最终结果
class BrowserAgent(AsyncFlow):
    async def post_async(self, shared, prep_res, exec_res):
        # 从exec_async获取结果，添加空值检查
        if exec_res is None:
            # 如果exec_res为None，检查迭代次数决定是否结束
            iteration = shared.get("iteration")
            if iteration is not None and iteration >= 30:  # 设置一个较大的最大迭代次数作为兜底
                return "finish"
            return "continue"  # 默认继续执行
            
        completion_result = exec_res.get("completion_result")
        iteration = exec_res.get("iteration")
        
        # 根据LLM的回答决定是否结束流程
        if completion_result and ("是" in completion_result.lower() or "完成" in completion_result.lower()):
            # 存储最终消息
            shared["final_message"] = "任务已完成：" + completion_result
            return "finish"
        
        # 如果LLM判断任务未完成，或者无法判断，则根据迭代次数决定
        if iteration is not None and iteration >= 30:  # 设置一个较大的最大迭代次数作为兜底
            shared["final_message"] = "达到最大迭代次数，任务结束"
            return "finish"
            
        # 继续执行
        return "continue"

# 创建各节点
get_env_node = AsyncGetBrowserEnv()
plan_node = AsyncPlanOperation()
execute_node = AsyncExecuteOperation()
observe_node = AsyncObserveResult()
finish_node = AsyncFinishNode()

# 连接各节点
get_env_node >> plan_node
plan_node >> execute_node
# 添加错误处理路径
execute_node - "success" >> observe_node
execute_node - "error" >> plan_node  # 错误时回到规划节点重新规划
execute_node - "retry" >> execute_node  # 临时性错误直接重试当前操作

observe_node - "continue" >> get_env_node
observe_node - "finish" >> finish_node

browser_agent = BrowserAgent(start=get_env_node)

async def main():
    global browser
    try:
        browser = BrowserAutomation()
    except Exception as e:
        print("Error initializing BrowserAutomation:", e)
        return

    shared = {
        "task": """去B站上传一个视频，\
        视频文件路径为 C:/Users/10170/Downloads/text3point.mp4 
        封面图片路径为 "C:/Users/10170/Pictures/test.jpg"
        更改完成后保存此视频草稿
        """,
        "operation_history": []
    }
    result = await browser_agent.run_async(shared)
    print("Final message:", result)
    print("Iterations:", shared.get("iteration"))

if __name__ == "__main__":
    asyncio.run(main())