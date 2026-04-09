# main.py

import warnings

from gui import TenderAnalyzerApp

# Suppress known urllib3/chardet compatibility warnings after imports
warnings.filterwarnings("ignore", message="urllib3.*or chardet.*doesn't match a supported version")

if __name__ == "__main__":
    app = TenderAnalyzerApp()
    app.mainloop()
