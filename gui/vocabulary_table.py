"""Vocabulary presentation; the source dictionaries remain owned by MainWindow."""
from PySide6.QtCore import QAbstractTableModel, QSortFilterProxyModel, Qt
from PySide6.QtGui import QColor
from services.vocabulary_workflow import entry_status
from utils.file_manager import check_audio_exists


class VocabularyModel(QAbstractTableModel):
    headers = ('英文搭配', '中文释义', '状态', '音频')

    def __init__(self, audio_dir, parent=None):
        super().__init__(parent)
        self.audio_dir = audio_dir
        self.rows = []
        self.presentation = []

    def replace(self, entries, generating):
        self.beginResetModel()
        self.rows = [dict(row) for row in entries]
        self.presentation = []
        for row in self.rows:
            status = '生成中' if row['word'] in generating else entry_status(row, self.audio_dir)
            audio = check_audio_exists(self.audio_dir, row['word'])
            self.presentation.append((status, audio))
        self.endResetModel()

    def rowCount(self, parent=None):
        return 0 if parent is not None and parent.isValid() else len(self.rows)

    def columnCount(self, parent=None):
        return 4

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return self.headers[section]

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        row = self.rows[index.row()]
        status, audio = self.presentation[index.row()]
        if role == Qt.ItemDataRole.UserRole:
            return row['word']
        if role == Qt.ItemDataRole.ToolTipRole:
            return '\n'.join(filter(None, (row['word'], row.get('translation'),
                '导入时间：' + row.get('imported_at', ''), status, row.get('generation_error'))))
        if role == Qt.ItemDataRole.ForegroundRole and index.column() == 2:
            return QColor('#f4aaaa' if status == '失败' else '#a5cdb2' if status == '已完成' else '#c4cad6')
        if role == Qt.ItemDataRole.DisplayRole:
            return (row['word'], row.get('translation', ''), status,
                    '齐全' if all(audio) else '缺搭配' if audio[1] else '缺例句' if audio[0] else '未生成')[index.column()]


class VocabularyFilter(QSortFilterProxyModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.query = ''
        self.status = '全部状态'

    def filterAcceptsRow(self, row, parent):
        model = self.sourceModel()
        entry = model.rows[row]
        return (not self.query or self.query in entry['word'].casefold()
                or self.query in entry.get('translation', '').casefold()) and (
                self.status == '全部状态' or model.presentation[row][0] == self.status)

    def apply(self, query, status):
        self.query, self.status = query.strip().casefold(), status
        self.invalidate()
