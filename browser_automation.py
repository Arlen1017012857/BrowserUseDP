from DrissionPage import ChromiumPage, ChromiumOptions
from loguru import logger
from typing import List, Optional, Callable, Union, Tuple
import time
import functools
from collections import deque
from dataclasses import dataclass
import os
from pathlib import Path

logger.add("browser_automation.log", format="{time} {level} {message}", level="ERROR")

class RepeatedCallError(Exception):
    """连续重复调用异常"""
    pass

@dataclass
class FunctionCall:
    """函数调用记录"""
    func_name: str
    args: tuple
    kwargs: dict
    timestamp: float

class FunctionCallTracker:
    """函数调用追踪器"""
    def __init__(self, max_history: int = 15, threshold_ms: int = 30000, max_repeats: int = 3):
        self.call_history = deque(maxlen=max_history)
        self.threshold_ms = threshold_ms
        self.max_repeats = max_repeats

    def __call__(self, func: Callable):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            current_time = time.time()
            
            # 记录当前调用
            call = FunctionCall(
                func_name=func.__name__,
                args=args,
                kwargs=kwargs,
                timestamp=current_time
            )
            
            # 检查重复调用次数
            repeat_count = self.count_recent_duplicates(call)
            
            # 构建警告信息
            warning_msg = (f"检测到函数 {func.__name__} 在 {self.threshold_ms}ms 内被连续调用 {repeat_count} 次，"
                         f"参数: args={args}, kwargs={kwargs}"
                         "，你可能在进行无效的操作，请改变执行方法")
            
            # 如果达到最大重复次数，抛出异常
            if repeat_count >= self.max_repeats:
                logger.error(warning_msg)
                raise RepeatedCallError(warning_msg)
            
            # 如果是倒数第二次调用，返回警告信息
            if repeat_count == self.max_repeats - 1:
                logger.warning(warning_msg)
                # 添加到历史记录
                self.call_history.append(call)
                # 执行函数但返回警告信息
                func(*args, **kwargs)
                return warning_msg
            
            # 检查是否存在重复调用
            if repeat_count > 0:
                logger.warning(f"检测到重复调用: {func.__name__}，这是第 {repeat_count + 1} 次调用")
                
            # 添加到历史记录
            self.call_history.append(call)
            
            # 执行原函数
            return func(*args, **kwargs)
            
        return wrapper
        
    def count_recent_duplicates(self, current_call: FunctionCall) -> int:
        """计算最近的重复调用次数
        
        Args:
            current_call: 当前函数调用信息
            
        Returns:
            int: 连续重复调用次数
        """
        if not self.call_history:
            return 0
            
        count = 0
        for prev_call in reversed(self.call_history):
            # 检查时间间隔
            time_diff = (current_call.timestamp - prev_call.timestamp) * 1000  # 转换为毫秒
            if time_diff > self.threshold_ms:
                break
                
            # 检查函数名和参数是否相同
            if (prev_call.func_name == current_call.func_name and
                prev_call.args == current_call.args and
                prev_call.kwargs == current_call.kwargs):
                count += 1
            else:
                break  # 如果遇到不同的调用就停止计数
                
        return count

class BrowserAutomation:
    def __init__(self):
        """初始化浏览器自动化类"""
        self._analyzer = None
        self.page = self._get_page()

    def _get_page(self):
        """获取或创建浏览器页面实例"""
        co = ChromiumOptions()
        # 阻止"自动保存密码"的提示气泡
        co.set_pref('credentials_enable_service', False)
        # 阻止"要恢复页面吗？Chrome未正确关闭"的提示气泡
        co.set_argument('--hide-crash-restore-bubble')
        # co.use_system_user_path()
        co.set_load_mode('normal')
        co.set_argument('--start-maximized')
        return ChromiumPage(co)

    def _load_dom_tree_js(self, page=None):
        """
        私有方法，将 build_dom_tree.js 加载到页面中，防止重复注入。
        如果页面已加载过该文件，则跳过加载过程。
        """
        import os
        if page is None:
            page = self.page
        # 检查页面是否已加载过 build_dom_tree.js，通过自定义标志 __domTreeJSLoaded
        loaded = page.run_js_loaded(
            "typeof window.__domTreeJSLoaded !== 'undefined' && window.__domTreeJSLoaded;",
            as_expr=True
        )
        if loaded:
            return
        script_path = os.path.join(os.path.dirname(__file__), "build_dom_tree.js")
        page.run_js_loaded(script_path)
        # 标记为已加载
        page.run_js_loaded("window.__domTreeJSLoaded = true;", as_expr=True)

    def get_tree(self, ele_or_page=None):
        from DrissionPage.common import tree
        if ele_or_page is None:
            ele_or_page = self.page.latest_tab
        result = tree(ele_or_page=ele_or_page, text=False, show_js=False, show_css=False)
        return result

    def find_element(self, xpath: str):
        """查找指定元素
        Args:
            xpath (str): 元素XPATH路径
        Returns:
            WebElement: 找到的元素
        """
        try:
            cleaned_xpath = self._clean_xpath(xpath)
            element = self.page.latest_tab.ele(cleaned_xpath)
            return element
        except Exception as e:
            logger.error(f"查找元素失败: {str(e)}")
            return None

    def switch_tab(self, title: Optional[str] = None, url: Optional[str] = None):
        """切换到指定标签页
        Args:
            title (str, optional): 标签页标题（包含匹配）
            url (str, optional): 标签页URL（包含匹配）
        Returns:
            Tab: 切换到的标签页
        """
        if not title and not url:
            raise ValueError("必须提供title或url参数")
        
        # 获取匹配的标签页
        tab = self.page.get_tab(title=title, url=url)
        if tab:
            # 激活并切换到该标签页
            self.page.activate_tab(tab)
            # if tab != self.page.latest_tab:
            #     try:
            #         logger.warning(f"当前标签页为 {self.page.latest_tab.title} - {self.page.latest_tab.url}, \n尝试切换到标签页失败: title='{title}', url='{url}'")
            #         logger.warning(f"尝试切换到最新标签页")
            #         from DrissionPage.common import Settings
            #         Settings.set_singleton_tab_obj(False)
            #         self.page.close_tabs(tab)
            #         self.page.new_tab(url)
            #         Settings.set_singleton_tab_obj(True)
            #     except Exception as e:
            #         logger.error(f"当前标签页为 {latest_tab.title} - {latest_tab.url}, \n切换到标签页失败: title='{title}', url='{url}'")
            # else:
            #     logger.info(f"当前标签页为 {latest_tab.title} - {latest_tab.url}, \n切换到标签页: {tab.title} - {tab.url}")
            return tab
        else:
            logger.error(f"未找到匹配的标签页: title='{title}', url='{url}'")
            return None

    @FunctionCallTracker()
    def go_to_url(self, url: str):
        """打开指定URL
        Args:
            url (str): 要访问的网址
        Returns:
            Tab: 打开的标签页
        """
        # 判断是否是有效的URL
        if not url.startswith("http://") and not url.startswith("https://"):
            raise ValueError("URL必须以http://或https://开头")
        # 判断url是否已经打开
        tabs = self.page.get_tabs(url=url)
        if tabs:
            logger.info(f"网页已打开: {url}")
            return self.switch_tab(url=url)
        else:
            tab = self.page.latest_tab
            tab.get(url)
            logger.info(f"成功打开网页: {url}")
            self.page.activate_tab(tab)
            return tab


    @FunctionCallTracker()
    def open_tab(self, url: str):
        """打开新标签页
        Args:
            url: 要打开的URL
        Returns:
            Tab: 打开的标签页
        """
        # 判断url是否已经打开
        existing_tabs = self.page.get_tabs(url=url)
        if existing_tabs:
            logger.info(f"网页已打开: {url}")
            tab = self.switch_tab(url=url)
        else:
            # 打开新标签页
            tab = self.page.new_tab(url=url)
            logger.info(f"打开新标签页: {url if url else '空白页面'}")
            self.page.activate_tab(tab)
        return tab

    @FunctionCallTracker()
    def close_tab(self, title: Optional[str] = None, url: Optional[str] = None):
        """关闭标签页
        Args:
            title (str, optional): 标签页标题（包含匹配）
            url (str, optional): 标签页URL（包含匹配）
        Returns:
            bool: 是否成功关闭标签页
        """
        tab = self.page.get_tab(title=title, url=url)
        if tab:
            self.page.close_tabs(tab)
            logger.info(f"成功关闭标签页： {tab.title} - {tab.url}")
            return True
        else:
            logger.error(f"未找到匹配的标签页: title='{title}', url='{url}'")
            return False

    # @FunctionCallTracker()
    def list_tabs(self):
        """列出所有标签页的标题和URL"""
        tabs = self.page.get_tabs()
        tab_info = []
        for i, tab in enumerate(tabs, 1):
            tab_info.append({
                'index': i,
                'title': tab.title,
                'url': tab.url
            })
            # logger.debug(f"标签页 {i}: {tab.title} - {tab.url}")
        return tab_info

    @FunctionCallTracker()
    def go_back(self):
        """返回上一页"""
        self.page.back()
        logger.info("返回到标签页： " + self.page.title)

    # @FunctionCallTracker()
    # def click_element(self, xpath: str):
    #     """点击指定元素
    #     Args:
    #         xpath (str): 元素XPATH路径
    #     """
    #     try:
    #         cleaned_xpath = self._clean_xpath(xpath)
    #         element = self.page.latest_tab.ele(cleaned_xpath)
    #         self.page.latest_tab.screencast.set_mode.frugal_imgs_mode()
    #         self.page.latest_tab.screencast.set_save_path("page_diff_imgs")
    #         before_url = self.page.latest_tab.url
    #         before = self.analyze_page(url=before_url)
    #         element.click()
    #         after = self.analyze_page(url=before_url)
    #         after_url = self.page.latest_tab.url

    #         return_msg = ""
    #         if before_url != after_url:
    #             return_msg = f"点击元素 {cleaned_xpath} 成功，页面URL变化： {before_url} -> {after_url}"
    #         compare_result =  self._compare_page_states(before, after)
    #         logger.info(f"点击元素: {cleaned_xpath} 成功")
    #         logger.info(f"页面元素变化:\n {compare_result}")
    #         return_msg += f"点击元素 {cleaned_xpath} 成功，页面元素变化:\n {compare_result}"
    #         return return_msg
    #     except Exception as e:
    #         logger.error(f"点击元素 {cleaned_xpath} 失败: {str(e)}")
    #         return f"error: 点击元素 {cleaned_xpath} 失败: {str(e)}, 请使用 analyze_page 或者 analyze_image 分析页面的可操作元素"

    @FunctionCallTracker()
    def click_element(self, xpath: str):
        """点击指定元素
        Args:
            xpath (str): 元素XPATH路径
        """
        try:
            cleaned_xpath = self._clean_xpath(xpath)
            # 判断点击元素是否会触发文件上传弹窗
            # normalized_xpath = cleaned_xpath.lower().replace(" ", "")
            # if ("input" in normalized_xpath) and ('type="file"' in normalized_xpath or "type='file'" in normalized_xpath):
            #     logger.info(f"点击元素 {cleaned_xpath} 会触发文件上传弹窗，不进行点击操作")
            #     return f"error: 点击元素 {cleaned_xpath} 会触发文件上传弹窗，请使用click_to_upload进行上传操作"
            
            element = self.page.latest_tab.ele(cleaned_xpath)
            if element:
                # self.page.latest_tab.screencast.set_mode.frugal_imgs_mode()
                # self.page.latest_tab.screencast.start(save_path="page_diff_imgs")
                element.click(by_js=True)
                self.page.wait.load_start()
                # self.page.latest_tab.screencast.stop(video_name=f"click_record", suffix="mp4")

                return_msg = f"点击{self.page.latest_tab.title} 页面中的元素 {cleaned_xpath} 成功"
                logger.info(return_msg)
                return return_msg
            else:
                logger.error(f"元素 {cleaned_xpath} 不存在")
                return f"error: 元素 {cleaned_xpath} 不存在"
        except Exception as e:
            logger.error(f"点击元素 {cleaned_xpath} 失败: {str(e)}")
            return f"error: 点击元素 {cleaned_xpath} 失败: {str(e)}, 请使用 analyze_page 或者 analyze_image 分析页面的可操作元素"

    @FunctionCallTracker()
    def input_text(self, xpath: str, text: str):
        """在指定元素中输入文本
        Args:
            xpath (str): 元素XPATH路径
            text (str): 要输入的文本
        """
        try:
            cleaned_xpath = self._clean_xpath(xpath)
            element = self.page.latest_tab.ele(cleaned_xpath)
            element.input(text+"\n", clear=True)
            logger.info(f"在元素 {cleaned_xpath} 中输入文本: {text}")
            return f"成功在{self.page.latest_tab.title} 页面中的元素 {cleaned_xpath} 中输入文本: {text} 并回车"
        except Exception as e:
            logger.error(f"输入文本失败: {str(e)}")
            return f"error: 输入文本失败 {cleaned_xpath} 失败原因: {str(e)}"

    def scroll_down(self, pixel: int = 300):
        """向下滚动
        Args:
            pixel (int): 滚动像素
        """
        self.page.scroll.down(pixel)
        logger.info(f"向下滚动 {pixel}px")

    def scroll_up(self, pixel: int = 300):
        """向上滚动
        Args:
            pixel (int): 滚动像素
        """
        self.page.scroll.up(pixel)
        logger.info(f"向上滚动 {pixel}px")

    def send_keys(self, xpath: str, keys: str):
        """发送键盘按键
        Args:
            xpath (str): 元素XPATH路径
            keys (str): 按键内容
        """
        try:
            cleaned_xpath = self._clean_xpath(xpath)
            element = self.page.ele(cleaned_xpath)
            element.input(keys, clear=True)
            logger.info(f"向标签页{self.page.title}中的元素 {cleaned_xpath} 发送按键: {keys}")
        except Exception as e:
            logger.error(f"发送按键失败: {str(e)}")

    def scroll_to_text(self, text: str):
        """滚动到包含指定文本的元素
        Args:
            text (str): 要查找的文本
        """
        try:
            XPATH = f"x://*[@text='{text}']"
            ele = self.page.ele(XPATH)
            ele.scroll.to_see()
            logger.info(f"滚动到文本: {text}")
        except Exception as e:
            logger.error(f"滚动到文本失败: {str(e)}")

    def get_dropdown_options(self, xpath: str) -> List[str]:
        """获取下拉框选项
        Args:
            xpath (str): 下拉框选择器
        Returns:
            List[str]: 选项列表
        """
        # 获取下拉框元素
        cleaned_xpath = self._clean_xpath(xpath)
        element = self.page.ele(cleaned_xpath)
        if element:
            options = element.eles('option')
            option_texts = [option.text for option in options]
            logger.info(f"获取下拉框 {cleaned_xpath} 的选项: {option_texts}")
            return option_texts
        else:
            logger.error(f"下拉框元素不存在: {xpath}")
            return []

    def select_dropdown_option(self, xpath: str, option_text: str):
        """选择下拉框选项
        Args:
            xpath (str): 下拉框选择器
            option_text (str): 选项文本
        """
        cleaned_xpath = self._clean_xpath(xpath)
        element = self.page.ele(cleaned_xpath)
        if element:
            options = element.eles('option')
            for option in options:
                if option.text == option_text:
                    option.click()
                    logger.info(f"在下拉框 {cleaned_xpath} 中选择选项: {option_text}")
                    return
            logger.error(f"未找到选项: {option_text}")
        else:
            logger.error(f"下拉框元素不存在: {xpath}")

    def highlight_element(self, xpath: str, color: str = "red", background: str = "yellow", duration: int = None):
        """高亮显示指定元素
        Args:
            xpath (str): 元素选择器
            color (str, optional): 边框颜色. 默认为 "red"
            background (str, optional): 背景颜色. 默认为 "yellow"
            duration (int, optional): 高亮持续时间(毫秒). 如果不指定，将一直保持高亮
        """
        cleaned_xpath = self._clean_xpath(xpath)
        element = self.page.ele(cleaned_xpath)
        if element:
            # 原有的高亮逻辑
            original_style = element.attr('style') or ''
            highlight_style = f"border: 2px solid {color} !important; background-color: {background} !important;"
            element.attr('style', original_style + highlight_style)
            
            if duration:
                import time
                time.sleep(duration / 1000)
                element.attr('style', original_style)
                
            logger.info(f"高亮显示元素: {cleaned_xpath}")
        else:
            logger.error(f"元素不存在: {xpath}")

    def get_clickable_elements(self, page=None, container_selector=None):
        """获取可点击元素

        Args:
            page: 页面实例
            container_selector: 容器选择器，用于只获取指定容器内的可点击元素

        Returns:
            list: 可点击元素的字符串列表（由 build_dom_tree.js 生成的描述信息）
        """
        from json import loads
        if page is None:
            page = self.page.latest_tab

        # 使用私有方法加载 JS 文件（避免重复注入）
        self.page.wait.load_start()
        self._load_dom_tree_js(page)
        
        # 构建 JS 调用，根据是否有容器选择器来调整参数
        if container_selector:
            js_code = f"""JSON.stringify({{ 
                element_str: get_clickable_elements(true, null, "{container_selector}").element_str 
            }});"""
        else:
            js_code = """JSON.stringify({ 
                element_str: get_clickable_elements(true).element_str 
            });"""
            
        # 执行 JS 函数，但只返回 element_str 属性来避免循环引用问题
        result_str = page.run_js_loaded(js_code, as_expr=True)
        result = loads(result_str)
        element_str = result.get("element_str", "")
        clickable_elements = element_str.split("\n") if element_str else []
        result = []
        for elem in clickable_elements:
            if not (elem.startswith('[') and ']:' in elem and elem[1].isdigit()):
                continue
                
            # 提取索引部分
            index_end = elem.index(']:')
            index = int(elem[1:index_end])
            
            # 提取完整的标签字符串
            tag_str = elem[index_end+2:]
            
            result.append({
                'index': index,
                'tag': tag_str
            })
        
        # time.sleep(0.5)
        # self.remove_highlight()
        return result

    def get_highlight_element(self, highlight_index: int):
        """获取高亮元素
        Args:
            highlight_index: 高亮元素索引

        Returns:
            dict: 高亮元素信息，包括 tagName、id、className、innerText 和 boundingRect
        """
        from json import loads

        page = self.page.latest_tab
        # 调用私有方法保证 JS 文件已加载
        self._load_dom_tree_js(page)

        js = f"""
        (function(index) {{
            var el = window.get_highlight_element(index);
            if (!el) return JSON.stringify(null);
            var rect = el.getBoundingClientRect();
            var info = {{
                tagName: el.tagName,
                id: el.id,
                className: el.className,
                innerText: el.innerText,
                boundingRect: {{
                    top: rect.top,
                    left: rect.left,
                    width: rect.width,
                    height: rect.height
                }}
            }};
            return JSON.stringify(info);
        }})({highlight_index});
        """
        result_str = page.run_js(js, as_expr=True)
        return loads(result_str)

    def remove_highlight(self):
        """移除高亮"""
        page = self.page.latest_tab
        # 调用私有方法保证 JS 文件已加载
        self._load_dom_tree_js(page)
        js = "remove_highlight();"
        page.run_js(js, as_expr=True)

    def close_browser(self):
        """关闭浏览器"""
        if self.page:
            self.page.quit()
            logger.info("浏览器已关闭")


    def search_google(self, query: str):
        """在Google中搜索
        Args:
            query (str): 搜索关键词
        """
        self.go_to_url("https://www.google.com")
        search_box_element = self.find_element("xpath://textarea[@name='q']")
        if search_box_element:
            self.input_text("xpath://textarea[@name='q']", query+'\n')
            logger.info(f"在Google中搜索: {query}")
        else:
            logger.error("未找到搜索框")

    def get_elements_xpath(self, element, show_unhighlighted=False, xpath_dict=None):
        """获取元素的XPath表达式字典
        
        Args:
            element: 元素信息字典
            show_unhighlighted: 是否包含未高亮元素
            xpath_dict: 内部递归使用的字典
        
        Returns:
            dict: 以元素索引为key，XPath表达式为value的字典
        """
        if xpath_dict is None:
            xpath_dict = {}
        
        # 首先处理当前元素
        if element.get('isInteractive'):
            highlight_index = element.get('highlightIndex')
            # 如果不显示未高亮元素且索引为None，则跳过当前元素
            if not show_unhighlighted and highlight_index is None:
                # 仍然需要处理子元素
                for child in element.get('children', []):
                    if child:  # 确保子元素不为None
                        self.get_elements_xpath(child, show_unhighlighted, xpath_dict)
                return xpath_dict
            
            text = element.get('text')
            if text:  # 只有当text不为None时才处理长度
                if len(text) > 50:
                    text = text[:47] + '...'
            
            tag_name = element.get('tagName', 'unknown')
            class_name = element.get('className')
        
            # 构建XPath表达式
            xpath_conditions = []
        
            # 添加class条件，支持多个class
            if class_name:
                classes = class_name.split()
                for cls in classes:
                    xpath_conditions.append(f"contains(@class, '{cls}')")
            
            # 根据文本来源生成对应的XPath条件
            if text:
                # 处理text中的特殊字符
                text = text.replace("'", "''")
                
                # 获取文本来源（从JavaScript传递过来）
                text_source = element.get('textSource') if isinstance(element, dict) else None
                
                if text_source == 'text':
                    # 直接文本内容匹配
                    xpath_conditions.append(f"normalize-space()='{text}'")
                elif text_source == 'child-text':
                    # 子元素文本匹配
                    xpath_conditions.append(f".//text()[normalize-space()='{text}']")
                elif text_source == 'value':
                    # value属性匹配
                    xpath_conditions.append(f"@value='{text}'")
                elif text_source == 'placeholder':
                    # placeholder属性匹配
                    xpath_conditions.append(f"@placeholder='{text}'")
                elif text_source == 'aria-label':
                    # aria-label属性匹配
                    xpath_conditions.append(f"@aria-label='{text}'")
                elif text_source == 'title':
                    # title属性匹配
                    xpath_conditions.append(f"@title='{text}'")
                elif text_source == 'alt':
                    # alt属性匹配
                    xpath_conditions.append(f"@alt='{text}'")
                elif text_source == 'name':
                    # name属性匹配
                    xpath_conditions.append(f"@name='{text}'")
                elif text_source == 'id':
                    # id属性匹配
                    xpath_conditions.append(f"@id='{text}'")
                elif text_source == 'option-text':
                    # select选项文本匹配
                    xpath_conditions.append(f"option[text()='{text}']")
                else:
                    # 如果没有特定来源信息，使用通用匹配
                    text_conditions = [
                        f"normalize-space()='{text}'",
                        f"@value='{text}'",
                        f"@placeholder='{text}'",
                        f"@aria-label='{text}'"
                    ]
                    xpath_conditions.append(f"({' or '.join(text_conditions)})")
            
            # 组合XPath条件
            xpath_str = f"//{tag_name}"
            if xpath_conditions:
                xpath_str += f"[{' and '.join(xpath_conditions)}]"
            
            # 如果有高亮索引，添加到结果字典
            if highlight_index is not None:
                xpath_dict[highlight_index] = xpath_str

    # 处理子元素
        for child in element.get('children', []):
            if child:  # 确保子元素不为None
                self.get_elements_xpath(child, show_unhighlighted, xpath_dict)
            
        return xpath_dict

    def validate_xpath(self, xpath: str, tab_title: str = None, tab_url: str = None) -> bool:
        """验证XPath表达式是否能准确定位到唯一元素
    
        Args:
            xpath: XPath表达式
            tab_title: 标签页标题（包含匹配），默认为当前标签页
            tab_url: 标签页URL（包含匹配），默认为当前标签页
        
        Returns:
            bool: 如果找到唯一一个元素返回True，否则返回False
        """
        try:
            current_tab = None
            
            # 如果指定了标签页，切换到指定标签页
            if tab_title or tab_url:
                current_tab = self.page.get_tab(title=tab_title, url=tab_url)
                if not current_tab:
                    logger.error(f"未找到匹配的标签页: title='{tab_title}', url='{tab_url}'")
                    return False
                self.page.activate_tab(current_tab)
                time.sleep(1)  # 等待标签页切换完成
            else:
                current_tab = self.page.latest_tab
            
            # 使用eles()方法查找所有匹配元素
            elements = current_tab.eles(xpath)
        
            # 检查是否只找到一个元素
            if len(elements) == 1:
                logger.info(f"在标签页 {current_tab.title} 中找到唯一匹配元素: {xpath}")
                return True, f"在标签页 {current_tab.title} 中找到唯一匹配元素: {xpath}"
            elif len(elements) > 1:
                logger.warning(f"在标签页 {current_tab.title} 中找到多个匹配元素: {xpath}")
                return False, f"在标签页 {current_tab.title} 中找到多个匹配元素: {xpath}"
            else:
                logger.warning(f"在标签页 {current_tab.title} 中未找到匹配元素: {xpath}")
                return False, f"在标签页 {current_tab.title} 中未找到匹配元素: {xpath}"
                
        except Exception as e:
            logger.error(f"验证XPath时发生错误: {str(e)}")
            return False, f"验证XPath时发生错误: {str(e)}"

    def validate_elements_xpath(self, xpath_dict: dict, tab_title: str = None, tab_url: str = None) -> dict:
        """批量验证多个XPath表达式
    
        Args:
            xpath_dict: 以索引为key、XPath表达式为value的字典
            tab_title: 标签页标题（包含匹配），默认为当前标签页
            tab_url: 标签页URL（包含匹配），默认为当前标签页
        
        Returns:
            dict: 验证结果字典，key为原始索引，value为验证结果(bool)
        """
        validation_results = {}
        for index, xpath in xpath_dict.items():
            validation_results[index] = self.validate_xpath(xpath, tab_title, tab_url)
        return validation_results

    @FunctionCallTracker()
    def click_to_upload(self, xpath: str, file_paths: Union[str, Path, List[str], Tuple[str, ...]], by_js = None) -> None:
        """点击元素并上传文件
        
        Args:
            xpath (str): 元素的XPath路径
            file_paths (Union[str, Path, List[str], Tuple[str, ...]]): 文件路径，支持单个路径或多个路径的列表/元组
            by_js (bool, optional): 是否使用JS方式点击. 为None时，如不被遮挡，用模拟点击，否则用 js 点击
            
        Raises:
            FileNotFoundError: 当文件路径不存在时抛出
            ValueError: 当文件路径为空或无效时抛出
        """
        # 获取元素
        cleaned_xpath = self._clean_xpath(xpath)
        element = self.find_element(cleaned_xpath)
        if not element:
            return f"未找到元素: {xpath}"
            
        # 验证并处理文件路径
        if isinstance(file_paths, (str, Path)):
            paths = [str(file_paths)]
        elif isinstance(file_paths, (list, tuple)):
            paths = [str(path) for path in file_paths]
        else:
            return f"file_paths必须是字符串、Path对象或它们的列表/元组: {file_paths}"
            
        # 验证所有文件是否存在
        for path in paths:
            if not os.path.exists(path):
                return f"文件不存在: {path}"
                
        # 如果是多个文件，用\n连接
        upload_path = '\n'.join(paths)
        
        try:
            # 调用元素的to_upload方法
            element.click.to_upload(upload_path, by_js=by_js)
            return f"成功上传文件: {upload_path}"
        except Exception as e:
            return f"上传文件失败: {str(e)}"

    def get_sceenshot(self, name="screenshot.jpeg"):
        """获取当前页面的截图"""
        tab = self.page.latest_tab
        return tab.get_screenshot(name=name)

    def _clean_xpath(self, xpath: str) -> str:
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

if __name__ == "__main__":
    # 测试代码
    tracker = FunctionCallTracker(max_repeats=3)

    @tracker
    def test_function():
        print("Hello, World!")
        time.sleep(0.1)  # 添加一个小延迟以便观察效果

    print("开始测试连续调用...")
    try:
        for i in range(4):
            print(f"\n第 {i + 1} 次调用:")
            test_function()
    except RepeatedCallError as e:
        print(f"\n捕获到异常: {e}")
