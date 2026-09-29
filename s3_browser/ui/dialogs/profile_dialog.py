"""AWS SSO Profile Manager Dialog.

Configures profiles with the 4 required SSO fields:
- start_url
- sso_region
- account_id
- role_name
"""

from __future__ import annotations

import threading
from typing import Any, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QVBoxLayout,
    QWidget,
)
import qtawesome as qta

from s3_browser.auth import SSOAuthManager
from s3_browser.config import ConfigManager, SSOProfile


class ProfileManagerDialog(QDialog):
    """Dialog to manage AWS SSO profiles and test authentication."""

    profile_changed = Signal(str)  # Emits current profile name on save

    def __init__(self, config_manager: ConfigManager, parent: Any = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("AWS SSO Profiles Manager")
        self.setMinimumSize(780, 500)
        self.resize(840, 540)
        self.config_manager = config_manager

        self._init_ui()
        self._load_profiles_into_list()

    def _init_ui(self) -> None:
        main_layout = QVBoxLayout(self)

        # Splitter: List on left, Form on right
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # Left side: Profiles list & buttons
        left_widget = QWidget()
        left_widget.setMinimumWidth(220)
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(6)

        left_label = QLabel("<b>Configured Profiles:</b>")
        self.profile_list = QListWidget()
        self.profile_list.currentItemChanged.connect(self._on_profile_selected)

        btn_box = QHBoxLayout()
        self.btn_add = QPushButton(qta.icon("fa5s.plus"), "New")
        self.btn_add.clicked.connect(self._create_new_profile)
        self.btn_delete = QPushButton(qta.icon("fa5s.trash"), "Delete")
        self.btn_delete.clicked.connect(self._delete_selected_profile)
        btn_box.addWidget(self.btn_add)
        btn_box.addWidget(self.btn_delete)

        left_layout.addWidget(left_label)
        left_layout.addWidget(self.profile_list)
        left_layout.addLayout(btn_box)

        # Right side: Edit form
        right_widget = QWidget()
        right_widget.setMinimumWidth(460)
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(10)

        form_group = QGroupBox("SSO Profile Settings")
        form_layout = QFormLayout(form_group)
        form_layout.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        form_layout.setFormAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        form_layout.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form_layout.setRowWrapPolicy(QFormLayout.RowWrapPolicy.DontWrapRows)
        form_layout.setHorizontalSpacing(14)
        form_layout.setVerticalSpacing(12)
        form_layout.setContentsMargins(16, 20, 16, 16)

        self.edit_name = QLineEdit()
        self.edit_start_url = QLineEdit()
        self.edit_start_url.setPlaceholderText("https://my-company.awsapps.com/start")

        self.edit_sso_region = QLineEdit()
        self.edit_sso_region.setPlaceholderText("us-east-1")

        self.edit_account_id = QLineEdit()
        self.edit_account_id.setPlaceholderText("12-digit AWS Account ID")

        self.edit_role_name = QLineEdit()
        self.edit_role_name.setPlaceholderText("e.g. ReadOnlyAccess")

        self.edit_default_region = QLineEdit()
        self.edit_default_region.setPlaceholderText("us-east-1")

        for edit in (
            self.edit_name,
            self.edit_start_url,
            self.edit_sso_region,
            self.edit_account_id,
            self.edit_role_name,
            self.edit_default_region,
        ):
            edit.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            edit.setMinimumHeight(28)
            edit.setMinimumWidth(260)

        form_layout.addRow("Profile Name:", self.edit_name)
        form_layout.addRow("SSO Start URL *:", self.edit_start_url)
        form_layout.addRow("SSO Region *:", self.edit_sso_region)
        form_layout.addRow("Account ID *:", self.edit_account_id)
        form_layout.addRow("Role Name *:", self.edit_role_name)
        form_layout.addRow("Default S3 Region:", self.edit_default_region)

        # Status and Action buttons
        self.lbl_status = QLabel("Enter the 4 required SSO fields to authenticate.")
        self.lbl_status.setWordWrap(True)
        self.lbl_status.setStyleSheet("color: #7f8c8d; font-style: italic;")

        action_box = QHBoxLayout()
        self.btn_test_login = QPushButton(qta.icon("fa5s.key"), "Login / Test SSO")
        self.btn_test_login.clicked.connect(self._test_sso_login)
        self.btn_save = QPushButton(qta.icon("fa5s.save"), "Save Profile")
        self.btn_save.setStyleSheet("font-weight: bold; padding: 4px 14px;")
        self.btn_save.clicked.connect(self._save_profile)

        action_box.addWidget(self.btn_test_login)
        action_box.addStretch()
        action_box.addWidget(self.btn_save)

        right_layout.addWidget(form_group)
        right_layout.addWidget(self.lbl_status)
        right_layout.addStretch()
        right_layout.addLayout(action_box)

        splitter.addWidget(left_widget)
        splitter.addWidget(right_widget)
        splitter.setChildrenCollapsible(False)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 3)
        splitter.setSizes([240, 580])

        main_layout.addWidget(splitter)

        # Bottom Close button
        bottom_box = QHBoxLayout()
        bottom_box.addStretch()
        btn_close = QPushButton("Close")
        btn_close.clicked.connect(self.accept)
        bottom_box.addWidget(btn_close)
        main_layout.addLayout(bottom_box)

    def _load_profiles_into_list(self) -> None:
        self.profile_list.clear()
        for name in sorted(self.config_manager.profiles.keys()):
            item = QListWidgetItem(qta.icon("fa5s.id-card"), name)
            self.profile_list.addItem(item)

        if self.profile_list.count() > 0:
            self.profile_list.setCurrentRow(0)
        else:
            self._create_new_profile()

    def _on_profile_selected(self, current: Optional[QListWidgetItem], previous: Optional[QListWidgetItem]) -> None:
        if not current:
            return
        name = current.text()
        prof = self.config_manager.profiles.get(name)
        if prof:
            self.edit_name.setText(prof.profile_name)
            self.edit_start_url.setText(prof.start_url)
            self.edit_sso_region.setText(prof.sso_region)
            self.edit_account_id.setText(prof.account_id)
            self.edit_role_name.setText(prof.role_name)
            self.edit_default_region.setText(prof.default_region or "us-east-1")
            self.lbl_status.setText("Profile loaded.")
            self.lbl_status.setStyleSheet("color: #27ae60;")

    def _create_new_profile(self) -> None:
        self.profile_list.clearSelection()
        self.edit_name.setText("New-SSO-Profile")
        self.edit_start_url.setText("")
        self.edit_sso_region.setText("us-east-1")
        self.edit_account_id.setText("")
        self.edit_role_name.setText("")
        self.edit_default_region.setText("us-east-1")
        self.lbl_status.setText("Fill in the fields and click 'Save Profile'.")
        self.lbl_status.setStyleSheet("color: #7f8c8d;")
        self.edit_start_url.setFocus()

    def _save_profile(self) -> None:
        name = self.edit_name.text().strip()
        url = self.edit_start_url.text().strip()
        sso_reg = self.edit_sso_region.text().strip()
        acc = self.edit_account_id.text().strip()
        role = self.edit_role_name.text().strip()
        def_reg = self.edit_default_region.text().strip() or "us-east-1"

        if not name or not url or not sso_reg or not acc or not role:
            QMessageBox.warning(self, "Missing Fields", "Please fill in all 4 SSO fields:\n- Start URL\n- SSO Region\n- Account ID\n- Role Name")
            return

        prof = SSOProfile(
            profile_name=name,
            start_url=url,
            sso_region=sso_reg,
            account_id=acc,
            role_name=role,
            default_region=def_reg,
        )
        self.config_manager.add_or_update_profile(prof)
        self.profile_changed.emit(name)
        self._load_profiles_into_list()
        # Find and select
        for i in range(self.profile_list.count()):
            if self.profile_list.item(i).text() == name:
                self.profile_list.setCurrentRow(i)
                break
        self.lbl_status.setText(f"Profile '{name}' saved successfully!")
        self.lbl_status.setStyleSheet("color: #27ae60; font-weight: bold;")

    def _delete_selected_profile(self) -> None:
        current = self.profile_list.currentItem()
        if not current:
            return
        name = current.text()
        ans = QMessageBox.question(self, "Confirm Delete", f"Are you sure you want to delete profile '{name}'?")
        if ans == QMessageBox.StandardButton.Yes:
            self.config_manager.delete_profile(name)
            self._load_profiles_into_list()

    def _test_sso_login(self) -> None:
        name = self.edit_name.text().strip()
        url = self.edit_start_url.text().strip()
        sso_reg = self.edit_sso_region.text().strip()
        acc = self.edit_account_id.text().strip()
        role = self.edit_role_name.text().strip()

        if not url or not sso_reg or not acc or not role:
            QMessageBox.warning(self, "Incomplete Profile", "Fill in the 4 SSO fields before testing login.")
            return

        prof = SSOProfile(
            profile_name=name or "test",
            start_url=url,
            sso_region=sso_reg,
            account_id=acc,
            role_name=role,
            default_region=self.edit_default_region.text().strip() or "us-east-1",
        )

        self.btn_test_login.setEnabled(False)
        self.lbl_status.setText("Initiating SSO login...")
        self.lbl_status.setStyleSheet("color: #2980b9;")

        def run_login() -> None:
            mgr = SSOAuthManager(prof)
            try:
                def cb(msg: str) -> None:
                    # Update status in UI
                    self.lbl_status.setText(msg)

                creds = mgr.get_role_credentials(force_login=True, status_callback=cb)
                self.lbl_status.setText(f"SSO Login Success! Key: {creds.access_key_id[:8]}... (Expires: {creds.expiration})")
                self.lbl_status.setStyleSheet("color: #27ae60; font-weight: bold;")
            except Exception as e:
                self.lbl_status.setText(f"SSO Login Failed: {e}")
                self.lbl_status.setStyleSheet("color: #e74c3c; font-weight: bold;")
            finally:
                self.btn_test_login.setEnabled(True)

        threading.Thread(target=run_login, daemon=True).start()
