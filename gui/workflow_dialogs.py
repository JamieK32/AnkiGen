from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QLabel, QListWidget, QListWidgetItem,
    QPlainTextEdit, QVBoxLayout,
)

from utils.file_manager import parse_import_text


class ImportDialog(QDialog):
    def __init__(self, known, parent=None, candidates=None):
        super().__init__(parent)
        self.setWindowTitle('选择文章搭配' if candidates is not None else '批量导入搭配')
        self.resize(760, 620)
        self.known = set(known)
        self.candidates = candidates
        layout = QVBoxLayout(self)
        self.mode = QComboBox()
        self.mode.addItem('逗号分隔（含逗号的搭配请用英文双引号包裹）', 'comma')
        self.mode.addItem('每行一条（保留搭配内部的逗号）', 'lines')
        self.text = QPlainTextEdit()
        self.text.setPlaceholderText('take off, "pursue safer, more nutritious and healthier food"')
        self.text.setMaximumHeight(170)
        layout.addWidget(self.mode)
        layout.addWidget(self.text)
        self.summary = QLabel()
        layout.addWidget(self.summary)
        self.preview = QListWidget()
        self.preview.setWordWrap(True)
        layout.addWidget(self.preview)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText('导入勾选项并生成')
        layout.addWidget(self.buttons)
        self.buttons.accepted.connect(self.accept)
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText('取消')
        self.buttons.rejected.connect(self.reject)
        self.text.textChanged.connect(self.refresh)
        self.mode.currentIndexChanged.connect(self.refresh)
        self.preview.itemChanged.connect(self.update_button)
        if candidates is not None:
            self.text.hide()
            self.mode.hide()
        self.refresh()

    def refresh(self):
        self.preview.clear()
        try:
            rows = self.candidates if self.candidates is not None else [
                {'word': word} for word in parse_import_text(self.text.toPlainText(), self.mode.currentData())
            ]
        except ValueError as exc:
            self.summary.setText(str(exc))
            self.update_button()
            return
        for row in rows:
            exists = row['word'] in self.known
            label = f"{'已有' if exists else '未收录'} · {row['word']}"
            if row.get('translation'):
                label += '\n' + row['translation']
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, row)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Unchecked if exists else Qt.CheckState.Checked)
            if exists:
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
            self.preview.addItem(item)
        self.summary.setText(f'解析到 {len(rows)} 条；已有条目自动跳过。可取消勾选不需要的搭配。')
        self.update_button()

    def selected_entries(self):
        return [dict(self.preview.item(i).data(Qt.ItemDataRole.UserRole))
                for i in range(self.preview.count())
                if self.preview.item(i).checkState() == Qt.CheckState.Checked]

    def update_button(self):
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(bool(self.selected_entries()))


class ArticleDialog(QDialog):
    def __init__(self, articles, parent=None):
        super().__init__(parent)
        self.setWindowTitle('从文章提取固定搭配')
        self.resize(780, 580)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel('粘贴中文、英文或双语文章；AI 提取后可勾选，原文会保存在本地。'))
        self.history = QComboBox()
        self.history.addItem('新文章', '')
        for article in reversed(articles):
            self.history.addItem(article['text'][:45].replace('\n', ' '), article['text'])
        layout.addWidget(self.history)
        self.text = QPlainTextEdit()
        self.text.setPlaceholderText('输入原文（最多 30,000 字符）')
        layout.addWidget(self.text)
        self.history.currentIndexChanged.connect(lambda: self.text.setPlainText(self.history.currentData()))
        self.count = QLabel()
        layout.addWidget(self.count)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText('提取候选搭配')
        self.buttons.accepted.connect(self.accept)
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText('取消')
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self.text.textChanged.connect(self.validate)
        self.validate()

    def validate(self):
        size = len(self.text.toPlainText().strip())
        self.count.setText(f'{size:,} / 30,000 字符')
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(0 < size <= 30000)


class SyncPreviewDialog(QDialog):
    def __init__(self, plan, deck, parent=None):
        super().__init__(parent)
        self.setWindowTitle('确认同步预览')
        self.resize(720, 560)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f'目标牌组：{deck}\n以本地词库为准；此次同步不会生成 AI 内容或音频。'))
        count = sum(len(ids) for ids in plan['delete'].values())
        layout.addWidget(QLabel(f"新增 {len(plan['create'])} · 更新 {len(plan['update'])} · 删除 {count} · 跳过 {len(plan['skipped'])}"))
        details = QPlainTextEdit()
        details.setReadOnly(True)
        sections = [('新增', plan['create']), ('更新', plan['update']),
                    ('删除（含重复笔记）', [f'{w} — {len(ids)} 条笔记' for w, ids in plan['delete'].items()]),
                    ('跳过：字段或音频不完整', plan['skipped'])]
        details.setPlainText('\n\n'.join(title + '\n' + ('\n'.join(rows) or '无') for title, rows in sections))
        layout.addWidget(details)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText('确认并同步')
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
