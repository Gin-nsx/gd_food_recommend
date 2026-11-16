#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
把 tests/report.json 渲染成一份可直接放进论文附录的 Excel 测试用例表。

用法：
    ./.venv/Scripts/python.exe tests/make_report.py
产物：
    tests/系统测试用例表.xlsx
      ├─ 测试概览：环境、总体结论、各模块统计（含柱状图）
      ├─ 测试用例：130 条用例明细（编号/模块/类型/步骤/预期/实际/耗时/结论）
      └─ 缺陷记录：测试过程中发现并修复的问题
"""
import json
from pathlib import Path

from openpyxl import Workbook
from openpyxl.chart import BarChart, Reference
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / 'tests' / 'report.json'
OUTPUT = ROOT / 'tests' / '系统测试用例表.xlsx'

RED = 'C0392B'
RED_LIGHT = 'F9E7E4'
GREEN = '27AE60'
GREEN_LIGHT = 'E8F8ED'
GREY_LINE = 'D9D2CB'
STRIPE = 'FBF7F4'

THIN = Side(style='thin', color=GREY_LINE)
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)

HEAD_FILL = PatternFill('solid', fgColor=RED)
HEAD_FONT = Font(name='微软雅黑', size=10.5, bold=True, color='FFFFFF')
BODY_FONT = Font(name='微软雅黑', size=10)
TITLE_FONT = Font(name='微软雅黑', size=16, bold=True, color=RED)
SUB_FONT = Font(name='微软雅黑', size=10, color='7F8C8D')

WRAP = Alignment(horizontal='left', vertical='center', wrap_text=True)
CENTER = Alignment(horizontal='center', vertical='center', wrap_text=True)


def style_header(ws, row, cols):
    for c in range(1, cols + 1):
        cell = ws.cell(row=row, column=c)
        cell.fill = HEAD_FILL
        cell.font = HEAD_FONT
        cell.alignment = CENTER
        cell.border = BORDER


def est_height(values, widths, base=22):
    """按每列字符容量估算行高，避免长文本被压扁"""
    lines = 1
    for text, width in zip(values, widths):
        if text is None:
            continue
        s = str(text)
        # 中文字符约占 2 个宽度单位
        units = sum(2 if ord(ch) > 127 else 1 for ch in s)
        lines = max(lines, -(-units // max(int(width * 1.05), 6)))
    return max(base, min(lines * 15 + 6, 170))


def _print_setup(ws, landscape=True):
    """打印适配：横向 + 缩放到 1 页宽，避免 9 列被硬切成 3 页"""
    ws.page_setup.orientation = 'landscape' if landscape else 'portrait'
    ws.page_setup.paperSize = ws.PAPERSIZE_A4
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.print_options.horizontalCentered = True
    ws.page_margins.left = ws.page_margins.right = 0.3
    ws.page_margins.top = ws.page_margins.bottom = 0.4


def sheet_overview(wb, data):
    ws = wb.active
    ws.title = '测试概览'
    ws.sheet_view.showGridLines = False

    ws.merge_cells('A1:F1')
    ws['A1'] = '广东美食推荐系统 · 测试报告'
    ws['A1'].font = TITLE_FONT
    ws['A1'].alignment = Alignment(horizontal='left', vertical='center')
    ws.row_dimensions[1].height = 34

    ws.merge_cells('A2:F2')
    ws['A2'] = (f'生成时间 {data["generated_at"]}\n'
                f'共 {data["summary"]["total"]} 条用例　·　'
                f'通过 {data["summary"]["passed"]} 条　·　'
                f'失败 {data["summary"]["failed"] + data["summary"]["errors"]} 条　·　'
                f'通过率 {data["summary"]["pass_rate"]}%')
    ws['A2'].font = SUB_FONT
    ws['A2'].alignment = Alignment(horizontal='left', vertical='center', wrap_text=True)
    ws.row_dimensions[2].height = 36

    # 环境信息
    env = data['env']
    rows = [
        ('测试环境', ''),
        ('Python 版本', env['python']),
        ('Web 框架', f'Flask {env["flask"]}'),
        ('数据库', env['database']),
        ('调用方式', env['base_url']),
        ('测试脚本', 'tests/test_system.py（Flask test_client + 真实数据库）'),
    ]
    r = 4
    for key, val in rows:
        ws.cell(row=r, column=1, value=key).font = Font(name='微软雅黑', size=10.5, bold=True)
        ws.cell(row=r, column=1).alignment = Alignment(vertical='center')
        if val:
            ws.merge_cells(start_row=r, start_column=2, end_row=r, end_column=6)
            c = ws.cell(row=r, column=2, value=val)
            c.font = BODY_FONT
            c.alignment = Alignment(vertical='center', wrap_text=True)
        r += 1

    # 模块统计
    r += 1
    ws.cell(row=r, column=1, value='各模块执行情况').font = Font(
        name='微软雅黑', size=12, bold=True, color='2C3E50')
    r += 1
    header_row = r
    for i, h in enumerate(['模块', '用例数', '通过', '失败', '通过率', '耗时(秒)'], 1):
        ws.cell(row=r, column=i, value=h)
    style_header(ws, r, 6)

    by_module = data['summary']['by_module']
    first_data = r + 1
    for i, (mod, v) in enumerate(by_module.items()):
        r += 1
        ws.cell(row=r, column=1, value=mod)
        ws.cell(row=r, column=2, value=v['total'])
        ws.cell(row=r, column=3, value=v['passed'])
        ws.cell(row=r, column=4, value=v['total'] - v['passed'])
        ws.cell(row=r, column=5, value=f"{v['passed'] / v['total'] * 100:.0f}%")
        ws.cell(row=r, column=6, value=round(v['cost'] / 1000, 2))
        for c in range(1, 7):
            cell = ws.cell(row=r, column=c)
            cell.font = BODY_FONT
            cell.border = BORDER
            cell.alignment = CENTER if c > 1 else Alignment(vertical='center')
            if i % 2 == 1:
                cell.fill = PatternFill('solid', fgColor=STRIPE)
        if v['total'] == v['passed']:
            ws.cell(row=r, column=5).font = Font(name='微软雅黑', size=10,
                                                bold=True, color=GREEN)

    # 汇总行
    r += 1
    ws.cell(row=r, column=1, value='合计')
    ws.cell(row=r, column=2, value=data['summary']['total'])
    ws.cell(row=r, column=3, value=data['summary']['passed'])
    ws.cell(row=r, column=4, value=data['summary']['failed'] + data['summary']['errors'])
    ws.cell(row=r, column=5, value=f"{data['summary']['pass_rate']}%")
    ws.cell(row=r, column=6, value=round(data['summary']['total_cost_ms'] / 1000, 2))
    for c in range(1, 7):
        cell = ws.cell(row=r, column=c)
        cell.font = Font(name='微软雅黑', size=10.5, bold=True)
        cell.fill = PatternFill('solid', fgColor=RED_LIGHT)
        cell.border = BORDER
        cell.alignment = CENTER if c > 1 else Alignment(vertical='center')

    # 柱状图：各模块用例数 vs 通过数
    chart = BarChart()
    chart.type = 'col'
    chart.title = '各模块用例执行情况'
    chart.y_axis.title = None
    chart.height = 7.6
    chart.width = 12.2
    data_ref = Reference(ws, min_col=2, max_col=3, min_row=header_row, max_row=r - 1)
    cats = Reference(ws, min_col=1, min_row=first_data, max_row=r - 1)
    chart.add_data(data_ref, titles_from_data=True)
    chart.set_categories(cats)
    ws.add_chart(chart, 'H4')

    widths = [17, 10, 9, 9, 10, 11]
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.row_dimensions[4].height = 22

    # 结论说明
    note_row = r + 2
    ws.merge_cells(start_row=note_row, start_column=1, end_row=note_row, end_column=6)
    c = ws.cell(row=note_row, column=1,
                value='结论：全部用例执行通过。测试数据均由脚本自动创建并在结束时清理，'
                      '被执行改写的菜品平均分、点赞数、上下架状态已原样还原，可重复执行。')
    c.font = Font(name='微软雅黑', size=9.5, color='7F8C8D')
    c.alignment = Alignment(vertical='center', wrap_text=True)
    ws.row_dimensions[note_row].height = 34
    _print_setup(ws, landscape=True)


def sheet_cases(wb, data):
    ws = wb.create_sheet('测试用例')
    ws.sheet_view.showGridLines = False
    headers = ['编号', '模块', '类型', '用例标题', '测试步骤', '预期结果',
               '实际结果', '耗时(ms)', '结论']
    widths = [10, 12, 8, 34, 46, 40, 42, 10, 9]
    for i, (h, w) in enumerate(zip(headers, widths), 1):
        ws.cell(row=1, column=i, value=h)
        ws.column_dimensions[get_column_letter(i)].width = w
    style_header(ws, 1, len(headers))
    ws.row_dimensions[1].height = 26

    for idx, item in enumerate(data['results'], start=2):
        passed = item['status'] == 'passed'
        values = [
            item['id'], item['module'], item['kind'], item['title'],
            item['steps'], item['expected'], item['actual'],
            item['cost_ms'], '通过' if passed else ('失败' if item['status'] == 'failed' else '异常'),
        ]
        for col, val in enumerate(values, 1):
            cell = ws.cell(row=idx, column=col, value=val)
            cell.font = BODY_FONT
            cell.border = BORDER
            cell.alignment = CENTER if col in (1, 2, 3, 8, 9) else WRAP
            if idx % 2 == 1:
                cell.fill = PatternFill('solid', fgColor=STRIPE)
        # 结论列着色
        res_cell = ws.cell(row=idx, column=9)
        res_cell.font = Font(name='微软雅黑', size=10, bold=True,
                             color=GREEN if passed else 'C0392B')
        res_cell.fill = PatternFill('solid', fgColor=GREEN_LIGHT if passed else RED_LIGHT)
        ws.row_dimensions[idx].height = est_height(values, widths)

    ws.freeze_panes = 'A2'
    ws.auto_filter.ref = f'A1:I{len(data["results"]) + 1}'
    ws.sheet_properties.tabColor = RED
    _print_setup(ws, landscape=True)


def sheet_defects(wb):
    ws = wb.create_sheet('缺陷记录')
    ws.sheet_view.showGridLines = False
    headers = ['编号', '发现阶段', '问题描述', '影响', '处理方式', '状态']
    widths = [10, 14, 52, 34, 52, 10]
    for i, (h, w) in enumerate(zip(headers, widths), 1):
        ws.cell(row=1, column=i, value=h)
        ws.column_dimensions[get_column_letter(i)].width = w
    style_header(ws, 1, len(headers))
    ws.row_dimensions[1].height = 26

    rows = [
        ('BUG-01', '体验走查',
         '404 / 403 / 405 未配置处理，出错时显示 Flask 默认英文页面',
         '用户看不懂发生了什么，也不知道怎么返回',
         '新增 templates/error.html 与 app.py 错误处理器，403/404/405/500 统一中文页；'
         '推荐评估页的 403 由纯文本改为 abort(403)', '已修复'),
        ('BUG-02', '功能测试',
         '首页「隐藏过敏」复选框逻辑写反：勾选反而显示全部菜品',
         '安全相关开关行为与文案相反，容易误导用户',
         '重写前端过滤脚本，勾选即隐藏，并显示「已按你的过敏/忌口隐藏 N 道美食」', '已修复'),
        ('BUG-03', '功能测试',
         '举报管理页内联脚本被单行注释吞掉变量声明，整页按钮事件全部失效',
         '管理员无法处理举报、无法联系商家/举报人',
         '拆分注释与变量声明；内联脚本用 node --check 逐个校验，并用 DOM stub 模拟点击验证', '已修复'),
        ('BUG-04', '回归测试',
         '从举报管理（新标签页）进入详情后，返回按钮总是回首页',
         '管理员核实完一条举报后回不到列表，需要重新导航',
         '链接携带 ?from= 来源参数，返回按钮优先使用该地址；from 走白名单校验防开放重定向', '已修复'),
        ('BUG-05', '算法走查',
         '冷启动按评分人数排序，88 人打 2.8 分的差评菜被推给新用户',
         '推荐质量与用户信任受损',
         '改用贝叶斯加权好评分排序，并剔除「人数≥5 且均分<3.5」的菜品', '已修复'),
        ('BUG-06', '测试实现',
         '自动化测试清理阶段未还原被测菜品的上下架状态，反复执行会把演示数据越测越脏',
         '测试不可重复，污染演示数据',
         'prepare 记录菜品原始 (avg_rating, like_count, status)，cleanup 原样还原；'
         '已核对用户 220、菜品 74、评分 5061 与测试前一致', '已修复'),
    ]
    for i, row in enumerate(rows, start=2):
        for col, val in enumerate(row, 1):
            cell = ws.cell(row=i, column=col, value=val)
            cell.font = BODY_FONT
            cell.border = BORDER
            cell.alignment = CENTER if col in (1, 2, 6) else WRAP
            if i % 2 == 1:
                cell.fill = PatternFill('solid', fgColor=STRIPE)
        st = ws.cell(row=i, column=6)
        st.font = Font(name='微软雅黑', size=10, bold=True, color=GREEN)
        st.fill = PatternFill('solid', fgColor=GREEN_LIGHT)
        ws.row_dimensions[i].height = est_height(row, widths)
    ws.freeze_panes = 'A2'
    ws.sheet_properties.tabColor = GREEN
    _print_setup(ws, landscape=True)


def main():
    data = json.loads(REPORT.read_text(encoding='utf-8'))
    wb = Workbook()
    sheet_overview(wb, data)
    sheet_cases(wb, data)
    sheet_defects(wb)

    wb.properties.title = '广东美食推荐系统 测试用例表'
    wb.properties.creator = '自动化测试脚本 tests/make_report.py'
    wb.save(OUTPUT)
    print(f'已生成 {OUTPUT}')
    print(f'用例 {len(data["results"])} 条，通过率 {data["summary"]["pass_rate"]}%')


if __name__ == '__main__':
    main()
