"""
so101.console — 增强的终端输出
==============================

使用 rich 库提供美观的终端输出。

使用示例：
    from so101.console import console, print_table, print_success, print_error
    
    # 直接使用
    console.print("Hello", style="bold green")
    
    # 便捷函数
    print_success("操作成功")
    print_error("操作失败", "请检查配置")
    
    # 表格
    print_table("设备列表", headers, rows)
"""

import sys
from typing import Optional, List, Any, Dict
from pathlib import Path

try:
    from rich.console import Console
    from rich.table import Table
    from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn
    from rich.panel import Panel
    from rich.syntax import Syntax
    from rich.tree import Tree
    from rich.markdown import Markdown
    from rich import box
    HAS_RICH = True
except ImportError:
    HAS_RICH = False

# ============================================================================
# Console 实例
# ============================================================================

if HAS_RICH:
    console = Console(stderr=True)  # 输出到 stderr，避免干扰管道
else:
    # 降级到普通 print
    class SimpleConsole:
        def print(self, *args, **kwargs):
            print(*args, file=sys.stderr)
    
    console = SimpleConsole()


# ============================================================================
# 便捷打印函数
# ============================================================================

def print_success(message: str, detail: str = ""):
    """打印成功信息"""
    if HAS_RICH:
        console.print(f"✓ {message}", style="bold green")
        if detail:
            console.print(f"  {detail}", style="dim")
    else:
        print(f"[OK] {message}", file=sys.stderr)
        if detail:
            print(f"  {detail}", file=sys.stderr)


def print_error(message: str, suggestion: str = ""):
    """打印错误信息"""
    if HAS_RICH:
        console.print(f"✗ {message}", style="bold red")
        if suggestion:
            console.print(f"  建议: {suggestion}", style="yellow")
    else:
        print(f"[ERROR] {message}", file=sys.stderr)
        if suggestion:
            print(f"  建议: {suggestion}", file=sys.stderr)


def print_warning(message: str, detail: str = ""):
    """打印警告信息"""
    if HAS_RICH:
        console.print(f"⚠ {message}", style="bold yellow")
        if detail:
            console.print(f"  {detail}", style="dim")
    else:
        print(f"[WARN] {message}", file=sys.stderr)
        if detail:
            print(f"  {detail}", file=sys.stderr)


def print_info(message: str, detail: str = ""):
    """打印信息"""
    if HAS_RICH:
        console.print(f"ℹ {message}", style="bold blue")
        if detail:
            console.print(f"  {detail}", style="dim")
    else:
        print(f"[INFO] {message}", file=sys.stderr)
        if detail:
            print(f"  {detail}", file=sys.stderr)


def print_step(step: int, total: int, message: str):
    """打印步骤信息"""
    if HAS_RICH:
        console.print(f"[{step}/{total}] {message}", style="bold cyan")
    else:
        print(f"[{step}/{total}] {message}", file=sys.stderr)


# ============================================================================
# 表格打印
# ============================================================================

def print_table(
    title: str,
    headers: List[str],
    rows: List[List[Any]],
    styles: Optional[Dict[str, str]] = None,
):
    """
    打印美观的表格。
    
    Args:
        title: 表格标题
        headers: 表头列表
        rows: 数据行列表
        styles: 列样式映射 {列名: 样式}
    """
    if not HAS_RICH:
        # 降级到简单表格
        print(f"\n{title}:", file=sys.stderr)
        print("  " + " | ".join(headers), file=sys.stderr)
        print("  " + "-" * (len(headers) * 15), file=sys.stderr)
        for row in rows:
            print("  " + " | ".join(str(x) for x in row), file=sys.stderr)
        print(file=sys.stderr)
        return
    
    table = Table(
        title=title,
        box=box.ROUNDED,
        show_header=True,
        header_style="bold magenta",
    )
    
    # 添加列
    for header in headers:
        style = styles.get(header, "") if styles else ""
        table.add_column(header, style=style)
    
    # 添加行
    for row in rows:
        table.add_row(*[str(x) for x in row])
    
    console.print(table)
    console.print()


# ============================================================================
# 进度条
# ============================================================================

def create_progress() -> Progress:
    """创建进度条"""
    if HAS_RICH:
        return Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            console=console,
        )
    else:
        # 降级到简单进度
        class SimpleProgress:
            def __enter__(self):
                return self
            
            def __exit__(self, *args):
                print(file=sys.stderr)
            
            def add_task(self, description, total=100):
                print(f"{description}...", file=sys.stderr)
                return 0
            
            def update(self, task_id, advance=1):
                pass
        
        return SimpleProgress()


# ============================================================================
# 面板和树
# ============================================================================

def print_panel(content: str, title: str = "", style: str = "blue"):
    """打印面板"""
    if HAS_RICH:
        panel = Panel(content, title=title, border_style=style)
        console.print(panel)
    else:
        print(f"\n=== {title} ===", file=sys.stderr)
        print(content, file=sys.stderr)
        print("=" * (len(title) + 8), file=sys.stderr)


def print_tree(title: str, items: Dict[str, Any], indent: int = 0):
    """打印树形结构"""
    if HAS_RICH:
        tree = Tree(title)
        for key, value in items.items():
            if isinstance(value, dict):
                branch = tree.add(key)
                for k, v in value.items():
                    branch.add(f"{k}: {v}")
            else:
                tree.add(f"{key}: {value}")
        console.print(tree)
    else:
        prefix = "  " * indent
        print(f"{prefix}{title}:", file=sys.stderr)
        for key, value in items.items():
            if isinstance(value, dict):
                print(f"{prefix}  {key}:", file=sys.stderr)
                for k, v in value.items():
                    print(f"{prefix}    {k}: {v}", file=sys.stderr)
            else:
                print(f"{prefix}  {key}: {value}", file=sys.stderr)


def print_markdown(text: str):
    """打印 Markdown"""
    if HAS_RICH:
        md = Markdown(text)
        console.print(md)
    else:
        print(text, file=sys.stderr)


def print_code(code: str, language: str = "python"):
    """打印语法高亮的代码"""
    if HAS_RICH:
        syntax = Syntax(code, language, theme="monokai")
        console.print(syntax)
    else:
        print(code, file=sys.stderr)


# ============================================================================
# 状态指示器
# ============================================================================

class StatusIndicator:
    """状态指示器上下文管理器"""
    
    def __init__(self, message: str):
        self.message = message
        self._status = None
    
    def __enter__(self):
        if HAS_RICH:
            self._status = console.status(self.message)
            self._status.__enter__()
        else:
            print(f"{self.message}...", file=sys.stderr)
        return self
    
    def __exit__(self, *args):
        if HAS_RICH and self._status:
            self._status.__exit__(*args)
    
    def update(self, message: str):
        """更新状态消息"""
        if HAS_RICH and self._status:
            self._status.update(message)
        else:
            print(f"  -> {message}", file=sys.stderr)


def status(message: str) -> StatusIndicator:
    """创建状态指示器"""
    return StatusIndicator(message)


# ============================================================================
# 确认提示
# ============================================================================

def confirm(message: str, default: bool = False) -> bool:
    """
    显示确认提示。
    
    Args:
        message: 提示消息
        default: 默认值
    
    Returns:
        用户选择
    """
    suffix = " [Y/n] " if default else " [y/N] "
    
    try:
        response = input(message + suffix).strip().lower()
        
        if not response:
            return default
        
        return response in ('y', 'yes', '是')
    except (KeyboardInterrupt, EOFError):
        print(file=sys.stderr)
        return False


# ============================================================================
# 环境检查
# ============================================================================

def check_rich_available() -> bool:
    """检查 rich 是否可用"""
    return HAS_RICH


def print_dependency_hint():
    """打印依赖提示"""
    if not HAS_RICH:
        print_info(
            "安装 rich 库可获得更好的终端体验",
            "pip install rich"
        )
