"""Presentation-only additions for the native qpdf Studio workbench."""

WORKBENCH_STYLES = """
QWidget { font-size: 12px; color: #1d1d1f; }
QMainWindow, QWidget#Root, QWidget#WorkspaceBody { background: #f5f5f7; }
QFrame#Toolbar { background: white; border: 1px solid #e5e5ea; border-radius: 12px; }
QFrame#WorkflowSidebar { background: white; border: 1px solid #e5e5ea; border-radius: 12px; }
QFrame#Inspector, QFrame#Panel { background: white; border: 1px solid #e5e5ea; border-radius: 12px; }
QLabel#Brand { font-size: 22px; color: #1d1d1f; font-weight: 600; }
QLabel#Heading { font-size: 13px; color: #1d1d1f; font-weight: 600; }
QLabel#ModeHeading { font-size: 17px; color: #1d1d1f; font-weight: 600; }
QLabel#WelcomeTitle { font-size: 28px; color: #1d1d1f; font-weight: 600; letter-spacing: -0.3px; }
QLabel#Dim, QLabel#WorkflowHint { color: #6e6e73; }
QLabel#PreviewBadge { background: #f5f5f7; color: #6e6e73; padding: 3px 8px; border-radius: 9px; }
QLabel#SignatureStatus { background: #fff6e5; color: #9a6700; padding: 8px; border-radius: 8px; }
QLabel#CertificateValue { color: #1d1d1f; padding: 1px 0 7px 0; }
QLabel#CertificateName { font-size: 11px; color: #86868b; }
QFrame#CertificateSummary { background: #f5f5f7; border: 0; border-radius: 10px; }
QFrame#SelectionEditor { background: white; border: 0; }
QFrame#SelectionEditor QLabel { background: transparent; }
QPushButton { min-height: 22px; padding: 6px 12px; border-radius: 8px; }
QPushButton#Primary { background: #0071e3; border: 1px solid #0071e3; color: white; }
QPushButton#Primary:hover { background: #0077ed; border-color: #0077ed; }
QPushButton#WorkflowStep { text-align: left; border: 0; background: transparent; padding: 8px 10px; border-radius: 8px; }
QPushButton#WorkflowStep:checked { background: #e8e8ed; color: #1d1d1f; font-weight: 600; }
QPushButton#WorkflowStep:hover { background: #f5f5f7; }
QToolButton { padding: 6px 12px; border-radius: 8px; }
QTabWidget#WorkspaceTabs::pane { border: 0; background: transparent; }
QTabWidget#WorkspaceTabs QTabBar { background: #e8e8ed; border-radius: 10px; }
QTabWidget#WorkspaceTabs QTabBar::tab { min-width: 72px; padding: 7px 16px; margin: 3px; background: transparent; color: #6e6e73; border: 0; border-radius: 8px; }
QTabWidget#WorkspaceTabs QTabBar::tab:selected { background: white; color: #1d1d1f; font-weight: 600; }
QTabWidget#RightTabs::pane { border: 0; background: white; }
QTabWidget#RightTabs QPlainTextEdit { font-family: "Microsoft YaHei UI", Consolas; font-size: 11px; }
QSplitter::handle { background: #f5f5f7; width: 8px; height: 8px; }
QScrollArea#WelcomeScroll { background: white; border: 1px solid #e5e5ea; border-radius: 16px; }
QWidget#WelcomeContent { background: white; }
"""

WORKBENCH_STYLES += """
QFrame#SigningStepCard { background: #f5f5f7; border: 0; border-radius: 12px; }
QLabel#SigningState { background: #e8f2fc; color: #0071e3; padding: 10px; border-radius: 8px; }
QLabel#SignatureStatusCard { background: #f5f5f7; color: #6e6e73; border-radius: 8px; padding: 8px 3px; font-size: 11px; }
QLabel#SignatureStatusCard[tone="success"] { background: #eaf8ee; color: #1f7a3a; }
QLabel#SignatureStatusCard[tone="error"] { background: #fdecec; color: #d70015; }
QLabel#SignatureStatusCard[tone="pending"] { background: #fff6e5; color: #9a6700; }
QTreeWidget { background: white; alternate-background-color: #fbfbfd; border: 1px solid #e5e5ea; border-radius: 8px; }
QTreeWidget::item { padding: 4px 2px; }
QTreeWidget::item:selected { background: #e8f2fc; color: #1d1d1f; }
"""

WORKBENCH_STYLES += """
QFrame#BSigningActions { background: #f5f5f7; border: 0; border-radius: 12px; }
QWidget#SignatureInspector { background: white; }
QWidget#SignatureInspector QComboBox { min-height: 25px; }
QWidget#SignatureInspector QTreeWidget::item { padding: 5px 2px; }
QWidget#SignatureInspector QLabel#SignatureStatusCard { padding: 6px 3px; }
"""


# Approved three-column signature layout; presentation only.
WORKBENCH_STYLES += """
QWidget#SignatureInformationWorkspace { background: #f5f5f7; }
QFrame#SignatureInformationCard { background: white; border: 1px solid #e5e5ea; border-radius: 12px; }
QFrame#SignatureInformationCard QLabel { border: 0; background: transparent; }
QPushButton#SignatureDetailLink { color: #0071e3; border: 0; background: transparent; padding: 3px 0; text-align: left; }
QWidget#SignatureVerificationCard { background: white; border: 1px solid #e5e5ea; border-radius: 12px; }
QWidget#SignatureVerificationCard QLabel { border: 0; }
QWidget#SignatureVerificationCard QLabel#SignatureStatusCard { border-radius: 8px; min-height: 32px; }
QWidget#SignatureVerificationCard[activeSlot="true"] { border: 1px solid #0071e3; }
"""
