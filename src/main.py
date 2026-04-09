# main.py

import warnings
warnings.filterwarnings("ignore", message="urllib3.*or chardet.*doesn't match a supported version")

from gui import TenderAnalyzerApp

if __name__ == "__main__":
    app = TenderAnalyzerApp()
    app.mainloop()
