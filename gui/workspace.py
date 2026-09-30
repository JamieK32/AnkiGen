"""Paged desktop workspace, sharing the existing workflow and worker services."""
import hashlib
import json

from PySide6.QtCore import QItemSelectionModel, QSettings, Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QMenu, QMessageBox, QPlainTextEdit, QProgressBar,
    QPushButton, QScrollArea, QSplitter, QStackedWidget, QTableView, QVBoxLayout, QWidget,
)
from gui.vocabulary_table import VocabularyModel, VocabularyFilter
from gui.word_editor import WordEditor
from services.vocabulary_workflow import extract_collocations, sync_preview
from utils.file_manager import save_words


def button(text, callback, primary=False):
    widget = QPushButton(text)
    if primary:
        widget.setObjectName('PrimaryAction')
    widget.clicked.connect(callback)
    return widget


def heading(layout, title, subtitle):
    label = QLabel(title)
    label.setObjectName('PageTitle')
    layout.addWidget(label)
    hint = QLabel(subtitle)
    hint.setWordWrap(True)
    hint.setObjectName('Muted')
    layout.addWidget(hint)


class Workspace:
    def _build_ui(self):
        self._apply_theme()
        self._sync_result = None
        self._selection_guard = False
        self._filter_state = ('', '全部状态')
        self._mutation_buttons = []
        self._page_index = 0
        root = QWidget()
        root.setObjectName('AppRoot')
        outer = QVBoxLayout(root)
        body = QHBoxLayout()
        rail = QVBoxLayout()
        brand = QLabel('AnkiGen')
        brand.setObjectName('PageTitle')
        rail.addWidget(brand)
        self.navigation = QListWidget()
        self.navigation.addItems(['词库', '文章提取', 'Anki 同步', '导入与备份'])
        self.navigation.setFixedWidth(136)
        self.navigation.setObjectName('Navigation')
        rail.addWidget(self.navigation, 1)
        self.settings_button = button('设置', self._on_open_settings_clicked)
        rail.addWidget(self.settings_button)
        body.addLayout(rail)
        self.pages = QStackedWidget()
        body.addWidget(self.pages, 1)
        outer.addLayout(body, 1)
        self._build_library_page()
        self._build_article_page()
        self._build_sync_page()
        self._build_files_page()

        task_row = QHBoxLayout()
        self.progress_label = QLabel('就绪')
        self.progress_label.setWordWrap(True)
        task_row.addWidget(self.progress_label, 1)
        self.log_toggle = button('展开日志', lambda: None)
        self.log_toggle.setCheckable(True)
        self.clear_log_button = button('清空日志', self._clear_log)
        task_row.addWidget(self.log_toggle)
        task_row.addWidget(self.clear_log_button)
        outer.addLayout(task_row)
        self.progress_bar = QProgressBar()
        self.progress_bar.setMaximumHeight(8)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.hide()
        outer.addWidget(self.progress_bar)
        self.log_widget = QPlainTextEdit()
        self.log_widget.setReadOnly(True)
        self.log_widget.setMaximumBlockCount(500)
        self.log_widget.setFixedHeight(140)
        self.log_widget.setPlaceholderText('任务详情与错误信息')
        self.log_widget.setToolTip('Ctrl+A 全选；Ctrl+C 复制')
        self.log_widget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.log_widget.customContextMenuRequested.connect(self._show_log_context_menu)
        outer.addWidget(self.log_widget)
        self.log_toggle.toggled.connect(self._toggle_log)
        self._toggle_log(False)
        self.setCentralWidget(root)
        self.navigation.setCurrentRow(0)
        self.navigation.currentRowChanged.connect(self._navigate)
        self.save_shortcut = QShortcut(QKeySequence.StandardKey.Save, self)
        self.save_shortcut.activated.connect(self._save_shortcut)
        for field in (self.editor.word_edit, self.editor.translation_edit, self.editor.phonetic_edit,
                      self.editor.part_of_speech_edit, self.editor.example_edit, self.editor.analysis_edit):
            field.textChanged.connect(self._update_dirty_indicator)
        self.editor.save_requested.connect(self._on_save_word_clicked)
        self.editor.play_word_audio_requested.connect(self._on_play_word_audio_clicked)
        self.editor.play_sentence_audio_requested.connect(self._on_play_sentence_audio_clicked)
        self._restore_ui_state()
        self._append_log('应用已启动。')

    def _new_page(self, title, subtitle):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(12)
        heading(layout, title, subtitle)
        self.pages.addWidget(page)
        return layout

    def _build_library_page(self):
        layout = self._new_page('词库', '筛选、整理搭配；选中一条查看详情，多选后批量处理。')
        tools = QHBoxLayout()
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText('搜索英文搭配或中文释义…')
        self.status_filter = QComboBox()
        self.status_filter.addItems(['全部状态', '待生成', '缺音频', '已完成', '失败', '生成中'])
        self.add_button = button('添加搭配', self._open_import)
        tools.addWidget(self.search_input, 1)
        tools.addWidget(self.status_filter)
        tools.addWidget(self.add_button)
        layout.addLayout(tools)
        actions = QHBoxLayout()
        self.selection_label = QLabel('未选择')
        self.generate_all_button = button('补全所选', lambda: self._generate_selected('complete'), True)
        self.audio_button = QPushButton('音频操作')
        menu = QMenu(self.audio_button)
        menu.addAction('仅补缺失音频', lambda: self._generate_selected('audio_missing'))
        menu.addAction('重新生成音频', lambda: self._generate_selected('audio_all'))
        self.audio_button.setMenu(menu)
        self.retry_button = button('重试失败', self._retry_failed)
        self.delete_button = button('删除', self._on_delete_word_clicked)
        actions.addWidget(self.selection_label)
        actions.addStretch()
        for b in (self.generate_all_button, self.audio_button, self.retry_button, self.delete_button):
            actions.addWidget(b)
        layout.addLayout(actions)
        self.splitter = QSplitter()
        self.word_list = QTableView()
        self.word_model = VocabularyModel(self.audio_dir, self)
        self.word_proxy = VocabularyFilter(self)
        self.word_proxy.setSourceModel(self.word_model)
        self.word_list.setModel(self.word_proxy)
        self.word_list.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.word_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.word_list.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.word_list.setShowGrid(False)
        self.word_list.setAlternatingRowColors(True)
        self.word_list.setWordWrap(True)
        self.word_list.verticalHeader().hide()
        self.word_list.verticalHeader().setDefaultSectionSize(52)
        self.word_list.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        self.word_list.horizontalHeader().setStretchLastSection(False)
        for i, width in enumerate((220, 150, 76, 76)):
            self.word_list.setColumnWidth(i, width)
        self.word_list.setMinimumWidth(300)
        self.splitter.addWidget(self.word_list)
        self.details = QStackedWidget()
        self.editor = WordEditor()
        self.editor.regenerate_audio_button.hide()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.editor)
        scroll.setMinimumWidth(310)
        detail_panel = QWidget()
        detail_layout = QVBoxLayout(detail_panel)
        detail_layout.setContentsMargins(0, 0, 0, 0)
        detail_layout.addWidget(scroll, 1)
        playback = QHBoxLayout()
        playback.addWidget(self.editor.play_word_button)
        playback.addWidget(self.editor.play_sentence_button)
        detail_layout.addLayout(playback)
        detail_layout.addWidget(self.editor.save_button)
        self.details.addWidget(detail_panel)
        self.selection_summary = QLabel('选择一条搭配查看详情')
        self.selection_summary.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.selection_summary.setWordWrap(True)
        self.details.addWidget(self.selection_summary)
        self.splitter.addWidget(self.details)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.setSizes([570, 400])
        layout.addWidget(self.splitter, 1)
        self.word_count_label = QLabel()
        self.word_count_label.setObjectName('Muted')
        layout.addWidget(self.word_count_label)
        self.search_input.textChanged.connect(self._filter_changed)
        self.status_filter.currentTextChanged.connect(self._filter_changed)
        self.word_list.selectionModel().selectionChanged.connect(self._on_word_selected)

    def _build_article_page(self):
        layout = self._new_page('文章提取', '粘贴原文 → 提取候选 → 勾选导入。原文会随搭配保存在本地。')
        self.article_history = QComboBox()
        self._load_article_history()
        layout.addWidget(self.article_history)
        captions = QHBoxLayout()
        captions.addWidget(QLabel('文章原文'), 1)
        captions.addWidget(QLabel('候选搭配 · 提取后勾选需要的条目'), 1)
        layout.addLayout(captions)
        split = QSplitter()
        self.article_text = QPlainTextEdit()
        self.article_text.setPlaceholderText('输入中文、英文或双语原文（最多 30,000 字符）')
        self.article_candidates = QListWidget()
        self.article_candidates.setWordWrap(True)
        split.addWidget(self.article_text)
        split.addWidget(self.article_candidates)
        layout.addWidget(split, 1)
        self.article_count = QLabel('0 / 30,000 字符；尚未提取')
        layout.addWidget(self.article_count)
        row = QHBoxLayout()
        self.article_button = button('提取候选搭配', self._extract_article, True)
        self.article_import = button('导入勾选项并生成', self._import_article_candidates)
        self.article_import.setEnabled(False)
        row.addWidget(self.article_button)
        row.addWidget(self.article_import)
        row.addStretch()
        layout.addLayout(row)
        self.article_text.textChanged.connect(self._article_controls)
        self.article_candidates.itemChanged.connect(self._article_controls)
        self.article_history.currentIndexChanged.connect(
            lambda: self.article_text.setPlainText(self.article_history.currentData() or ''))
        self._article_controls()

    def _load_article_history(self):
        path = self.data_dir / 'articles.json'
        self._article_history_error = ''
        try:
            self._articles = json.loads(path.read_text(encoding='utf-8')) if path.exists() else []
            if not isinstance(self._articles, list) or any(not isinstance(a, dict) or not isinstance(a.get('text'), str) for a in self._articles):
                raise ValueError('文章历史格式不正确')
        except (ValueError, OSError) as exc:
            self._articles = []
            self._article_history_error = f'文章历史无法读取，请先修复或恢复备份：{exc}'
        self.article_history.blockSignals(True)
        self.article_history.clear()
        self.article_history.addItem('新文章', '')
        for article in reversed(self._articles):
            self.article_history.addItem(article['text'][:55].replace('\n', ' '), article['text'])
        self.article_history.blockSignals(False)

    def _article_controls(self, *args):
        size = len(self.article_text.toPlainText().strip())
        count = len(self._chosen_candidates())
        self.article_count.setText(f'{size:,} / 30,000 字符 · 已勾选 {count} 条候选')
        self.article_button.setEnabled(not self._task_busy and 0 < size <= 30000)
        self.article_import.setEnabled(not self._task_busy and bool(count))

    def _chosen_candidates(self):
        return [dict(self.article_candidates.item(i).data(Qt.ItemDataRole.UserRole))
                for i in range(self.article_candidates.count())
                if self.article_candidates.item(i).checkState() == Qt.CheckState.Checked]

    def _extract_article(self):
        if self._task_busy or not self._confirm_unsaved():
            return
        if self._article_history_error:
            self._show_error(self._article_history_error)
            return
        if not self.gpt_generator:
            self._show_error('请先在设置中配置 API 密钥。')
            return
        text = self.article_text.toPlainText().strip()
        if not 0 < len(text) <= 30000:
            return
        generator = self.gpt_generator

        def task(progress, log):
            log('正在提取文章搭配…')
            return extract_collocations(generator, text)

        def finished(rows):
            if not any(a['text'] == text for a in self._articles):
                self._articles.append({'text': text, 'imported_at': self._now_timestamp()})
                save_words(self.data_dir / 'articles.json', self._articles)
                self._load_article_history()
            self.article_candidates.clear()
            known = {w['word'] for w in self.words}
            for row in rows:
                row = dict(row, source_text=text)
                exists = row['word'] in known
                item = QListWidgetItem(('已有 · ' if exists else '') + row['word'] + '\n' + row.get('translation', ''))
                item.setData(Qt.ItemDataRole.UserRole, row)
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(Qt.CheckState.Unchecked if exists else Qt.CheckState.Checked)
                if exists:
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
                self.article_candidates.addItem(item)
            self._article_controls()
            self.progress_label.setText(f'提取完成：{len(rows)} 条候选，请在文章页确认导入')

        self._start_task('正在提取文章搭配…', task, finished, allow_browse_audio=True)

    def _import_article_candidates(self):
        if self._task_busy or not self._confirm_unsaved():
            return
        rows = self._chosen_candidates()
        self._import_entries(rows)
        known = {w['word'] for w in self.words}
        for i in range(self.article_candidates.count()):
            item = self.article_candidates.item(i)
            if item.data(Qt.ItemDataRole.UserRole)['word'] in known:
                item.setCheckState(Qt.CheckState.Unchecked)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
        self._article_controls()

    def _build_sync_page(self):
        layout = self._new_page('Anki 同步', '以整个本地词库为准同步；词库页的筛选和选择不影响同步范围。')
        self.deck_label = QLabel()
        self.deck_label.setWordWrap(True)
        layout.addWidget(self.deck_label)
        row = QHBoxLayout()
        self.anki_status = button('Anki：未检测', self._check_anki_connection)
        self.anki_status.setToolTip('请先启动 Anki，并安装 AnkiConnect 插件。点击检测连接。')
        self.sync_button = button('刷新同步预览', self._preview_sync)
        self.sync_confirm = button('确认并同步', self._execute_preview, True)
        self.sync_confirm.setEnabled(False)
        for b in (self.anki_status, self.sync_button, self.sync_confirm):
            row.addWidget(b)
        row.addStretch()
        layout.addLayout(row)
        self.sync_summary = QLabel('尚未预览。同步前请检查新增、更新和删除明细。')
        self.sync_summary.setWordWrap(True)
        layout.addWidget(self.sync_summary)
        self.sync_details = QPlainTextEdit()
        self.sync_details.setReadOnly(True)
        layout.addWidget(self.sync_details, 1)
        self.sync_outcome = QLabel('同步不会生成 AI 内容或音频；不完整条目会跳过。')
        self.sync_outcome.setWordWrap(True)
        layout.addWidget(self.sync_outcome)
        self._invalidate_sync()

    def _sync_fingerprint(self):
        media = [(p.name, p.stat().st_size, p.stat().st_mtime_ns) for p in sorted(self.audio_dir.glob('*.mp3'))]
        return json.dumps([self.words, self.anki_url, self.deck_name, self.model_name, media], sort_keys=True)

    def _invalidate_sync(self):
        self._sync_result = None
        self.sync_confirm.setEnabled(False)
        self.sync_summary.setText('请刷新同步预览；本地内容或设置变化后需要重新预览。')
        self.sync_details.clear()
        self.deck_label.setText(f'目标牌组：{self.deck_name}    笔记类型：{self.model_name}')

    def _preview_sync(self):
        if self._task_busy or not self._confirm_unsaved():
            return
        snapshot = [dict(x) for x in self.words]
        fingerprint = self._sync_fingerprint()
        self._invalidate_sync()
        self.anki_status.setText('Anki：检测中…')

        def task(progress, log):
            self.anki_api.check_connection()
            remote = self.anki_api.get_deck_word_to_note_ids(self.deck_name)
            return {'remote': remote, 'plan': sync_preview(snapshot, remote, self.audio_dir)}

        def finished(result):
            self.anki_status.setText('Anki：已连接')
            if fingerprint != self._sync_fingerprint():
                return
            self._sync_result = (fingerprint, result)
            plan = result['plan']
            count = sum(len(ids) for ids in plan['delete'].values())
            self.sync_summary.setText(f"新增 {len(plan['create'])} · 更新 {len(plan['update'])} · 删除 {count} · 跳过 {len(plan['skipped'])}")
            sections = [('新增', plan['create']), ('更新', plan['update']),
                        ('删除（含重复笔记）', [f'{w} — {len(ids)} 条笔记' for w, ids in plan['delete'].items()]),
                        ('跳过：字段或音频不完整', plan['skipped'])]
            self.sync_details.setPlainText('\n\n'.join(title + '\n' + ('\n'.join(rows) or '无') for title, rows in sections))
            self.sync_confirm.setEnabled(True)

        self._start_task('正在读取 Anki 同步预览…', task, finished, allow_browse_audio=True)

    def _execute_preview(self):
        if self._task_busy or not self._confirm_unsaved() or not self._sync_result:
            return
        fingerprint, result = self._sync_result
        if fingerprint != self._sync_fingerprint():
            self._invalidate_sync()
            return
        count = sum(len(ids) for ids in result['plan']['delete'].values())
        if count and QMessageBox.question(self, '确认删除 Anki 笔记',
                f'此次同步将删除 {count} 条 Anki 笔记。请确认已检查预览中的删除明细。',
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        self._invalidate_sync()
        self._on_sync_to_anki_clicked(result['remote'])

    def _build_files_page(self):
        layout = self._new_page('导入与备份', '与其他工具交换内容，或完整保存词库以备恢复、换电脑。')
        heading(layout, '文件交换', 'Excel / CSV 可保存释义、例句等字段供编辑；TXT 仅包含英文，一行一条。音频请使用完整备份。')
        row = QHBoxLayout()
        self.library_button = button('导入文件…', self._import_library_file, True)
        self.export_all = button('导出全部…', lambda: self._export_library_file(False))
        self.export_selected = button('导出所选…', lambda: self._export_library_file(True))
        for b in (self.library_button, self.export_all, self.export_selected):
            row.addWidget(b)
        row.addStretch()
        layout.addLayout(row)
        heading(layout, '完整备份与恢复', 'ZIP 包含词条、释义、例句、来源原文和音频，不包含 API 密钥。恢复会替换本地内容，并先自动备份当前词库。')
        row = QHBoxLayout()
        self.backup_button = button('创建完整备份…', self._backup_library)
        self.restore_button = button('从备份恢复…', self._restore_library)
        row.addWidget(self.backup_button)
        row.addWidget(self.restore_button)
        row.addStretch()
        layout.addLayout(row)
        hint = QLabel('英文词汇文件自动更新：exports/vocabulary-english.txt\n每次保存词库后更新，无需手动导出。')
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.file_outcome = QLabel('')
        self.file_outcome.setWordWrap(True)
        self.file_outcome.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.file_outcome)
        layout.addStretch()
        self._mutation_buttons.extend([self.library_button, self.export_all, self.export_selected,
                                       self.backup_button, self.restore_button])

    def _navigate(self, index):
        if not self._confirm_unsaved():
            self.navigation.blockSignals(True)
            self.navigation.setCurrentRow(self._page_index)
            self.navigation.blockSignals(False)
            return
        self._page_index = index
        self.pages.setCurrentIndex(index)

    def _save_shortcut(self):
        if not self._task_busy and self._editing_word and self.editor.save_button.isEnabled():
            self._on_save_word_clicked(self.editor.get_word_data())

    def _selected_words(self):
        return [str(i.data(Qt.ItemDataRole.UserRole)) for i in self.word_list.selectionModel().selectedRows()]

    def _current_selected_word(self):
        words = self._selected_words()
        return words[0] if len(words) == 1 else None

    def _select_words(self, words):
        selection = self.word_list.selectionModel()
        selection.clearSelection()
        for row in range(self.word_proxy.rowCount()):
            index = self.word_proxy.index(row, 0)
            if index.data(Qt.ItemDataRole.UserRole) in words:
                selection.select(index, QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows)
                selection.setCurrentIndex(index, QItemSelectionModel.SelectionFlag.NoUpdate)

    def _filter_changed(self, *args):
        if not self._confirm_unsaved():
            for control, value in ((self.search_input, self._filter_state[0]), (self.status_filter, self._filter_state[1])):
                control.blockSignals(True)
                control.setText(value) if isinstance(control, QLineEdit) else control.setCurrentText(value)
                control.blockSignals(False)
            return
        self._filter_state = (self.search_input.text(), self.status_filter.currentText())
        self._selection_guard = True
        self.word_list.clearSelection()
        self.word_proxy.apply(*self._filter_state)
        self._selection_guard = False
        self._on_word_selected()
        self._update_count()

    def _refresh_word_list(self, select_word=None):
        if not self._confirm_unsaved():
            return
        selected = [select_word] if isinstance(select_word, str) else self._selected_words()
        initial = self.word_model.rowCount() == 0 and not self._filter_state[0]
        self._filter_state = (self.search_input.text(), self.status_filter.currentText())
        self._selection_guard = True
        self.word_model.replace(self.words, self._generating_words)
        self.word_proxy.apply(self.search_input.text(), self.status_filter.currentText())
        if not selected and initial and self.word_proxy.rowCount():
            selected = [self.word_proxy.index(0, 0).data(Qt.ItemDataRole.UserRole)]
        self._select_words(selected)
        self._selection_guard = False
        self._on_word_selected()
        self._update_count()
        self._invalidate_sync()

    def _update_count(self):
        self.word_count_label.setText(f'共 {len(self.words)} 条 · 当前显示 {self.word_proxy.rowCount()} 条 · Ctrl+A 全选当前结果')

    def _on_word_selected(self, *args):
        if self._selection_guard:
            return
        if not self._confirm_unsaved():
            self._selection_guard = True
            self._select_words([self._editing_word])
            self._selection_guard = False
            return
        selected = self._selected_words()
        self._editor_baseline = None
        self._editing_word = None
        self.selection_label.setText(f'已选 {len(selected)} 条' if selected else '未选择')
        if len(selected) == 1:
            row = self._find_word(selected[0])
            self.editor.set_word_data(row, False, False)
            self._editing_word = row['word']
            self._editor_baseline = self.editor.get_word_data()
            self._update_audio_status(row['word'])
            self.details.setCurrentIndex(0)
        else:
            self.editor.clear()
            self.selection_summary.setText(f'已选择 {len(selected)} 条搭配\n使用上方按钮批量补全、生成音频或删除。'
                                           if selected else '选择一条搭配查看详情\n可通过搜索或状态筛选快速定位。')
            self.details.setCurrentIndex(1)
        self._update_controls()

    def _update_controls(self):
        busy = self._task_busy
        selected = self._selected_words()
        for b in (self.generate_all_button, self.audio_button, self.delete_button):
            b.setEnabled(not busy and bool(selected))
        self.retry_button.setEnabled(not busy and any(w.get('generation_error') for w in self.words if w['word'] in selected))
        self.export_selected.setEnabled(not busy and bool(selected))
        self.editor.set_interaction_mode(not busy and len(selected) == 1,
            len(selected) == 1 and (not busy or self._busy_allows_browse_audio))
        self._update_dirty_indicator()

    def _set_busy(self, busy, status, allow_browse_audio=False):
        self._task_busy = busy
        self._busy_allows_browse_audio = busy and allow_browse_audio
        self.progress_bar.setVisible(busy)
        self.progress_bar.setRange(0, 0 if busy else 100)
        for b in self._mutation_buttons + [self.add_button, self.settings_button, self.sync_button, self.anki_status]:
            b.setEnabled(not busy)
        self.sync_confirm.setEnabled(not busy and self._sync_result is not None)
        self.article_text.setReadOnly(busy)
        self.article_history.setEnabled(not busy)
        self.article_candidates.setEnabled(not busy)
        for control in (self.search_input, self.status_filter, self.word_list):
            control.setEnabled(not busy or allow_browse_audio)
        self._article_controls()
        self._update_controls()
        self.statusBar().showMessage(status)

    def _ui_settings(self):
        # Separate from API settings and vocabulary backups; each checkout has its own layout.
        key = hashlib.sha256(str(self.project_root.resolve()).encode()).hexdigest()[:16]
        return QSettings('AnkiGen', 'Workspace-' + key)

    def _restore_ui_state(self):
        state = self._ui_settings()
        if state.contains('geometry'):
            self.restoreGeometry(state.value('geometry'))
        if state.contains('splitter'):
            self.splitter.restoreState(state.value('splitter'))
        if state.contains('columns'):
            self.word_list.horizontalHeader().restoreState(state.value('columns'))

    def _save_ui_state(self):
        state = self._ui_settings()
        state.setValue('geometry', self.saveGeometry())
        state.setValue('splitter', self.splitter.saveState())
        state.setValue('columns', self.word_list.horizontalHeader().saveState())
