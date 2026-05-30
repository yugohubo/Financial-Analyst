import sys
import os
import json
import datetime
import traceback
import webbrowser
import urllib.parse
from typing import List, Dict, Any, Optional
import pandas as pd

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QListWidget, QListWidgetItem, QLabel, QLineEdit, QPushButton,
    QTextBrowser, QTabWidget, QSplitter, QComboBox, QDialog,
    QFormLayout, QMessageBox, QFrame, QTextEdit, QInputDialog,
    QProgressBar, QScrollArea, QSizePolicy, QTableWidget, QTableWidgetItem,
    QHeaderView, QFileDialog, QCheckBox
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QSize, QTimer
from PyQt6.QtGui import QColor, QFont, QIcon, QTextCursor

# Matplotlib integration in Qt6
import matplotlib
matplotlib.use("QtAgg")
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
import matplotlib.pyplot as plt
import mplcyberpunk

# Core modules
import database
from bridge_agent import BridgeAgent
from scraper_integration import InProcessScraper
from analysis_engine import AnalysisEngine
from vector_store import LightweightVectorStore

# --- Background Worker Thread ---

class ResearchWorker(QThread):
    """
    Worker thread that handles downloading historical data, running Bridge Agent search,
    scraping articles in-process, RAG vector indexing, and compiling the LLM Analyst report.
    Prevents the main PyQt6 GUI thread from freezing.
    """
    log_signal = pyqtSignal(str, str, str, str)  # timestamp, agent, level, message
    progress_signal = pyqtSignal(int, str)  # percent, message
    finished_signal = pyqtSignal(bool, str, str)  # success, symbol, message
    
    def __init__(self, symbol: str, name: str, model_name: str) -> None:
        super().__init__()
        self.symbol = symbol
        self.name = name
        self.model_name = model_name
        self.bridge_agent = BridgeAgent()
        self.scraper = InProcessScraper()
        self.analysis_engine = AnalysisEngine()

    def emit_log(self, agent: str, level: str, message: str):
        timestamp = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        database.save_log(agent, level, message)
        self.log_signal.emit(timestamp, agent, level, message)

    def run(self) -> None:
        import asyncio
        try:
            # Execute the entire asynchronous flow in a single unified event loop
            asyncio.run(self.async_run())
        except Exception as e:
            err_trace = traceback.format_exc()
            self.emit_log("System", "ERROR", f"Error in research worker for {self.symbol}: {str(e)}\n{err_trace}")
            self.finished_signal.emit(False, self.symbol, f"Araştırma hatayla sonuçlandı: {str(e)}")

    async def async_run(self) -> None:
        self.progress_signal.emit(10, f"[{self.symbol}] Geçmiş piyasa verileri indiriliyor...")
        self.emit_log("System", "INFO", f"Research started for {self.name} ({self.symbol})")
        
        # Step 1: Fetch historical price data (90 days)
        df = self.analysis_engine.fetch_historical_data(self.symbol, period_days=90)
        if df.empty:
            raise ValueError("Yahoo Finance historical price data could not be downloaded.")
        
        metrics = self.analysis_engine.calculate_indicators(df)
        self.progress_signal.emit(30, f"[{self.symbol}] Teknik göstergeler ve ARIMA tahmini hesaplanıyor...")
        
        # Step 2: Run ARIMA forecasting
        forecast_data = self.analysis_engine.run_arima_forecast(df)
        
        # Step 3: Discover relevant articles via Bridge Agent (DuckDuckGo Search)
        self.progress_signal.emit(50, f"[{self.symbol}] İnternette makroekonomik haberler araştırılıyor...")
        discovered_articles = self.bridge_agent.discover_articles(self.symbol, self.name, max_results=3)
        
        # Step 4: Scrape found URLs and index them
        self.progress_signal.emit(70, f"[{self.symbol}] Haber içerikleri kazınıyor ve yerel veritabanına kaydediliyor...")
        scraped_count = 0
        for art in discovered_articles:
            url = art["url"]
            title = art["title"]
            source = art["source"]
            
            try:
                # Perform in-process scrape asynchronously using await directly!
                scraped_data = await self.scraper.scrape_url(url)
                
                content = scraped_data.get("content", "")
                
                # Post-scrape content relevance density verification to block 'dirty' or empty data
                if not self.bridge_agent.verify_content_relevance(self.symbol, content):
                    self.emit_log("Fast Scraper", "WARNING", f"Skipping low-relevance clickbait/SPA shell: [{source}]")
                    continue
                    
                author = scraped_data.get("author", "Bilinmeyen")
                pub_date = scraped_data.get("publication_date", datetime.date.today().strftime('%Y-%m-%d'))
                
                # Deduce basic sentiment heuristics for database filing
                sentiment = "NEUTRAL"
                if len(content) > 100:
                    score = self.analysis_engine._simple_sentiment_deduct(content)
                    sentiment = "BULLISH" if score > 0.15 else "BEARISH" if score < -0.15 else "NEUTRAL"
                
                # Save to SQLite
                database.save_article(
                    symbol=self.symbol,
                    url=url,
                    title=title,
                    author=author,
                    pub_date=pub_date,
                    content=content,
                    sentiment=sentiment,
                    summary=art.get("snippet", "")[:400]
                )
                scraped_count += 1
                self.emit_log("Fast Scraper", "INFO", f"Saved and indexed article from [{source}]: {title[:35]}...")
            except Exception as scrape_err:
                self.emit_log("Fast Scraper", "WARNING", f"Could not scrape {url}: {str(scrape_err)}")
        
        self.emit_log("System", "INFO", f"Scraped and indexed {scraped_count} articles for {self.symbol}.")
        
        # Step 5: Generate LLM report using local Ollama (or fallback)
        self.progress_signal.emit(90, f"[{self.symbol}] Yapay zekalı makroekonomist raporu hazırlanıyor...")
        
        # Directly await the async Ollama report generator inside the single loop
        report, sentiment = await self.analysis_engine.generate_ollama_report(
            self.symbol, self.name, metrics, forecast_data, selected_model=self.model_name
        )
        
        self.progress_signal.emit(100, f"[{self.symbol}] Araştırma tamamlandı!")
        self.emit_log("System", "INFO", f"Autonomous research cycle finished successfully for {self.symbol}.")
        self.finished_signal.emit(True, self.symbol, "Araştırma başarıyla tamamlandı ve rapor kaydedildi.")

# --- Dynamic Suggestion Worker Thread ---

class SuggestionWorker(QThread):
    suggestions_signal = pyqtSignal(list)
    
    def __init__(self, query: str) -> None:
        super().__init__()
        self.query = query
        
    def run(self):
        import httpx
        try:
            # Add user-agent header to bypass Yahoo Finance scraping blocks
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
            }
            url = f"https://query2.finance.yahoo.com/v1/finance/search?q={self.query}"
            response = httpx.get(url, headers=headers, timeout=4.0)
            if response.status_code == 200:
                quotes = response.json().get("quotes", [])
                self.suggestions_signal.emit(quotes)
        except Exception:
            pass

# --- Dynamic Add Instrument Dialog ---

class AddInstrumentDialog(QDialog):
    """Modern dark-themed popup dialog with real-time Yahoo Finance autocomplete suggestions."""
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Yeni Enstrüman Ekle")
        self.setFixedSize(480, 520)
        self.finished_workers_pool = []
        self.init_ui()

    def init_ui(self):
        # Dialog Style Sheet
        self.setStyleSheet("""
            QDialog {
                background-color: #1c1c22;
                color: #f8f8f2;
                border: 1px solid #2c2c35;
            }
            QLabel {
                color: #f8f8f2;
                font-weight: bold;
                font-size: 12px;
            }
            QLineEdit {
                background-color: #121214;
                color: #f8f8f2;
                border: 1px solid #2c2c35;
                border-radius: 4px;
                padding: 6px;
                font-size: 13px;
            }
            QLineEdit:focus {
                border: 1px solid #2979ff;
            }
            QComboBox {
                background-color: #121214;
                color: #f8f8f2;
                border: 1px solid #2c2c35;
                border-radius: 4px;
                padding: 6px;
            }
            QListWidget {
                background-color: #121214;
                border: 1px solid #2c2c35;
                border-radius: 4px;
                color: #f8f8f2;
            }
            QListWidget::item {
                padding: 8px;
                border-bottom: 1px solid #1c1c22;
                border-radius: 4px;
            }
            QListWidget::item:hover {
                background-color: #22232a;
                color: #00e676;
            }
            QListWidget::item:selected {
                background-color: #2979ff;
                color: #ffffff;
            }
            QPushButton {
                background-color: #2979ff;
                color: white;
                font-weight: bold;
                border-radius: 4px;
                padding: 8px;
                border: none;
            }
            QPushButton:hover {
                background-color: #2962ff;
            }
        """)

        layout = QVBoxLayout(self)
        form_layout = QFormLayout()
        
        self.symbol_input = QLineEdit()
        self.symbol_input.setPlaceholderText("Sembol arayın veya yazın... Örn: USDTRY, AAPL, BTC")
        
        self.suggestions_list = QListWidget()
        self.suggestions_list.setFixedHeight(150)
        self.suggestions_list.itemClicked.connect(self.on_suggestion_clicked)
        
        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("Görünen Ad otomatik doldurulur...")
        
        self.category_input = QComboBox()
        self.category_input.addItems(["Hisse Senedi", "Forex", "Kripto Para", "Emtia", "Küresel Endeks", "Yerel Endeks", "Makro Gösterge", "Diğer"])
        
        form_layout.addRow("Sembol Ara / Giriş:", self.symbol_input)
        form_layout.addRow("Arama Sonuçları:", self.suggestions_list)
        form_layout.addRow("Görünen Adı:", self.name_input)
        form_layout.addRow("Kategori:", self.category_input)
        
        layout.addLayout(form_layout)
        layout.addSpacing(10)
        
        button_layout = QHBoxLayout()
        self.save_btn = QPushButton("Ekle")
        self.save_btn.clicked.connect(self.accept)
        self.cancel_btn = QPushButton("İptal")
        self.cancel_btn.setStyleSheet("background-color: #3e3f4b;")
        self.cancel_btn.clicked.connect(self.reject)
        
        button_layout.addWidget(self.cancel_btn)
        button_layout.addWidget(self.save_btn)
        
        layout.addLayout(button_layout)

        # 300ms Debounce Timer to prevent flooding Yahoo Finance API on every keystroke
        self.debounce_timer = QTimer(self)
        self.debounce_timer.setSingleShot(True)
        self.debounce_timer.timeout.connect(self.fetch_suggestions)
        
        self.symbol_input.textChanged.connect(lambda: self.debounce_timer.start(300))

    def fetch_suggestions(self):
        """Spawns SuggestionWorker background QThread to fetch real-time suggestions."""
        query = self.symbol_input.text().strip()
        if len(query) < 2:
            self.suggestions_list.clear()
            return
            
        self.suggestion_worker = SuggestionWorker(query)
        self.finished_workers_pool.append(self.suggestion_worker) # Keep alive to prevent dummy thread warnings
        
        def handle_suggestions(quotes: list):
            self.suggestions_list.clear()
            if not quotes:
                self.suggestions_list.addItem("Sonuç bulunamadı.")
                return
                
            for q in quotes:
                symbol = q.get("symbol", "")
                name = q.get("longname") or q.get("shortname") or symbol
                quote_type = q.get("quoteType", "Diğer")
                exch = q.get("exchDisp", "")
                
                display_text = f"{symbol}  •  {name}  [{quote_type} - {exch}]"
                item = QListWidgetItem(display_text)
                # Save whole data structure inside custom data role
                item.setData(Qt.ItemDataRole.UserRole, q)
                self.suggestions_list.addItem(item)
                
        self.suggestion_worker.suggestions_signal.connect(handle_suggestions)
        self.suggestion_worker.start()

    def on_suggestion_clicked(self, item: QListWidgetItem):
        """Auto-populates fields on clicking suggestion list item, guessing categories dynamically."""
        q = item.data(Qt.ItemDataRole.UserRole)
        if not q or not isinstance(q, dict):
            return
            
        symbol = q.get("symbol", "")
        name = q.get("longname") or q.get("shortname") or symbol
        quote_type = q.get("quoteType", "")
        
        # Block signals temporarily to prevent launching a suggestion loop on auto-populating
        self.symbol_input.blockSignals(True)
        self.symbol_input.setText(symbol)
        self.symbol_input.blockSignals(False)
        
        self.name_input.setText(name)
        
        # Smart category resolver mapping quote types directly to application categories
        category_map = {
            "CRYPTOCURRENCY": "Kripto Para",
            "CURRENCY": "Forex",
            "EQUITY": "Hisse Senedi",
            "ETF": "Hisse Senedi",
            "FUTURE": "Emtia",
            "INDEX": "Küresel Endeks",
            "MUTUALFUND": "Diğer"
        }
        
        category = category_map.get(quote_type, "Diğer")
        # Custom index check for Turkey specific symbols (.IS)
        if symbol.endswith(".IS"):
            category = "Yerel Endeks" if quote_type == "INDEX" else "Hisse Senedi"
            
        idx = self.category_input.findText(category)
        if idx >= 0:
            self.category_input.setCurrentIndex(idx)

    def get_data(self) -> Dict[str, str]:
        return {
            "symbol": self.symbol_input.text().strip(),
            "name": self.name_input.text().strip(),
            "category": self.category_input.currentText()
        }

class SettingsDialog(QDialog):
    """Modern dark-themed popup dialog for configuring Connection and API settings."""
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Bağlantı ve API Ayarları")
        self.setFixedSize(500, 360)
        self.init_ui()
        self.load_settings()

    def init_ui(self):
        # Dialog Style Sheet (Dark Cyberpunk Theme)
        self.setStyleSheet("""
            QDialog {
                background-color: #1c1c22;
                color: #f8f8f2;
                border: 1px solid #2c2c35;
            }
            QLabel {
                color: #f8f8f2;
                font-weight: bold;
                font-size: 12px;
            }
            QLineEdit {
                background-color: #121214;
                color: #f8f8f2;
                border: 1px solid #2c2c35;
                border-radius: 4px;
                padding: 6px;
                font-size: 13px;
            }
            QLineEdit:focus {
                border: 1px solid #2979ff;
            }
            QPushButton {
                background-color: #2979ff;
                color: white;
                font-weight: bold;
                border-radius: 4px;
                padding: 8px;
                border: none;
            }
            QPushButton:hover {
                background-color: #2962ff;
            }
            QPushButton#test_btn {
                background-color: #3e3f4b;
                border: 1px solid #2c2c35;
                color: #f8f8f2;
            }
            QPushButton#test_btn:hover {
                background-color: #22232a;
                border: 1px solid #00e676;
                color: #00e676;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setSpacing(15)

        header = QLabel("⚡ BULUT YAPAY ZEKA BAĞLANTISI")
        header.setStyleSheet("color: #00e676; font-size: 14px; font-weight: bold; margin-bottom: 5px;")
        layout.addWidget(header)

        # Gemini API Key Input
        self.key_lbl = QLabel("Google Gemini API Anahtarı:")
        layout.addWidget(self.key_lbl)

        self.key_input = QLineEdit()
        # Model selection dropdown
        self.model_label = QLabel("Model Seçimi:")
        self.model_combo = QComboBox()
        self.model_combo.addItems(["Google Gemini (Bulut)", "Local Ollama (GPT-OSS 120B)"])
        model_layout = QHBoxLayout()
        model_layout.addWidget(self.model_label)
        model_layout.addWidget(self.model_combo)
        layout.addLayout(model_layout)
        self.key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.key_input.setPlaceholderText("AI Studio API Anahtarınızı buraya yapıştırın...")
        layout.addWidget(self.key_input)

        self.strict_checkbox = QCheckBox("Strict Mode (Yalnızca güvenli verileri işle)")
        layout.addWidget(self.strict_checkbox)

        # Link to Google AI Studio
        self.link_lbl = QLabel("<a href='https://aistudio.google.com' style='color: #2979ff; text-decoration: none;'>30 Saniyede Ücretsiz Gemini API Key Alın (AI Studio) ↗</a>")
        self.link_lbl.setOpenExternalLinks(True)
        self.link_lbl.setStyleSheet("font-size: 11px;")
        layout.addWidget(self.link_lbl)

        description = QLabel(
            "API anahtarı girildiğinde sistem, otonom analizleri ışık hızında buluttan (Gemini 2.0 Flash) çözer. "
            "Kutu boş bırakılırsa sırasıyla yerel Ollama motoru veya tamamen çevrimdışı yedek motor kullanılır."
        )
        description.setWordWrap(True)
        description.setStyleSheet("color: #8f90a6; font-size: 11px; line-height: 1.4;")
        layout.addWidget(description)

        layout.addStretch()

        # Action Buttons
        button_layout = QHBoxLayout()
        self.test_btn = QPushButton("🔌 Bağlantıyı Test Et")
        self.test_btn.setObjectName("test_btn")
        self.test_btn.clicked.connect(self.test_connection)

        self.cancel_btn = QPushButton("İptal")
        self.cancel_btn.setStyleSheet("background-color: #3e3f4b;")
        self.cancel_btn.clicked.connect(self.reject)

        self.save_btn = QPushButton("Kaydet")
        self.save_btn.clicked.connect(self.save_settings)

        button_layout.addWidget(self.test_btn)
        button_layout.addStretch()
        button_layout.addWidget(self.cancel_btn)
        button_layout.addWidget(self.save_btn)

        layout.addLayout(button_layout)

    def load_settings(self):
        """Loads settings from database into fields."""
        gemini_key = database.get_setting("gemini_api_key", "")
        self.key_input.setText(gemini_key)
        # Load selected model from settings
        model_choice = database.get_setting("model_choice", "Google Gemini (Bulut)")
        idx = self.model_combo.findText(model_choice)
        if idx >= 0:
            self.model_combo.setCurrentIndex(idx)
        
        # Load strict mode
        is_strict = database.get_setting("strict_mode", "false") == "true"
        self.strict_checkbox.setChecked(is_strict)

    def save_settings(self):
        """Saves values into database settings table."""
        api_key = self.key_input.text().strip()
        database.save_setting("gemini_api_key", api_key)
        # Save selected model
        selected_model = self.model_combo.currentText()
        database.save_setting("model_choice", selected_model)
        # Save strict mode setting
        strict_val = "true" if self.strict_checkbox.isChecked() else "false"
        database.save_setting("strict_mode", strict_val)
        QMessageBox.information(self, "Başarılı", "API ayarları veritabanına başarıyla kaydedildi!")
        self.accept()

    def test_connection(self):
        """Tests the Gemini API connection with a simple request using httpx (trying 2.0-flash first, fallback to 1.5-flash)."""
        api_key = self.key_input.text().strip()
        if not api_key:
            QMessageBox.warning(self, "Hata", "Lütfen test etmeden önce bir API anahtarı girin!")
            return

        self.test_btn.setEnabled(False)
        # Disable model selection during test
        self.model_combo.setEnabled(False)
        self.test_btn.setText("📡 Bağlanılıyor...")
        QApplication.processEvents()

        # Import httpx dynamically for the connection test
        import httpx
        try:
            headers = {"Content-Type": "application/json"}
            payload = {
                "contents": [{
                    "parts": [{"text": "Hello, please confirm you are working."}]
                }]
            }
            
            models_to_try = ["gemini-2.0-flash", "gemini-1.5-flash"]
            response = None
            last_err = ""
            
            for model in models_to_try:
                for verify_ssl in [True, False]:
                    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
                    try:
                        res = httpx.post(url, json=payload, headers=headers, timeout=5.0, verify=verify_ssl)
                        if res.status_code == 200:
                            response = res
                            break
                        else:
                            last_err = f"Model {model} error {res.status_code} (SSL verify: {verify_ssl}): {res.text[:150]}"
                    except Exception as e:
                        last_err = f"Model {model} exception (SSL verify: {verify_ssl}): {str(e)}"
                if response is not None:
                    break
            
            if response is not None:
                QMessageBox.information(self, "Bağlantı Başarılı", "✅ Google Gemini bulut bağlantısı başarıyla sağlandı! API anahtarı aktif.")
            else:
                QMessageBox.critical(self, "Bağlantı Başarısız", f"❌ Bulut bağlantısı kurulamadı. Hata:\n{last_err}")
        except Exception as e:
            QMessageBox.critical(self, "Bağlantı Hatası", f"❌ Sunucuya bağlanılamadı:\n{str(e)}")
        finally:
            self.test_btn.setEnabled(True)
            self.model_combo.setEnabled(True)
            self.test_btn.setText("🔌 Bağlantıyı Test Et")

# --- Embedded Matplotlib Canvas Widget ---

class MplCanvas(FigureCanvas):
    """Interactive Matplotlib canvas integrated into PyQt6, utilizing Cyberpunk glowing visuals."""
    def __init__(self, width: int = 7, height: int = 5, dpi: int = 100) -> None:
        self.fig = Figure(figsize=(width, height), dpi=dpi, facecolor='#121214')
        super().__init__(self.fig)
        
        # Configure overall figure properties
        self.fig.subplots_adjust(hspace=0.35, bottom=0.12, top=0.92, left=0.1, right=0.95)
        
        # Subplot 1: Price and ARIMA Forecast
        self.ax_price = self.fig.add_subplot(2, 1, 1)
        self.ax_price.set_facecolor('#121214')
        
        # Subplot 2: RSI Oscillator
        self.ax_rsi = self.fig.add_subplot(2, 1, 2)
        self.ax_rsi.set_facecolor('#121214')

    def plot_data(self, symbol: str, name: str, price_df: pd.DataFrame, forecast_dict: Optional[Dict[str, Any]] = None):
        """Draws historical prices, moving averages, and ARIMA forecast bands with a premium cyberpunk look."""
        self.ax_price.clear()
        self.ax_rsi.clear()
        
        if price_df.empty:
            # Draw placeholder when no data loaded yet
            self.ax_price.text(0.5, 0.5, "Piyasa Verisi Bekleniyor...\nLütfen 'Araştır' Butonuna Tıklayarak Ajanı Başlatın.",
                               color='#8f90a6', ha='center', va='center', transform=self.ax_price.transAxes, fontsize=12)
            self.ax_rsi.text(0.5, 0.5, "RSI Hesaplanıyor...", color='#8f90a6', ha='center', va='center', transform=self.ax_rsi.transAxes, fontsize=11)
            self.draw()
            return

        try:
            # Apply cyberpunk styling
            plt.style.use("dark_background")
            
            # --- Draw Main Price Chart ---
            dates = price_df.index
            close_prices = price_df['Close']
            
            # Draw historical close line
            line_close, = self.ax_price.plot(dates, close_prices, label="Fiyat", color="#00e676", linewidth=2.0)
            
            # Compute SMAs on the fly for plot consistency
            sma_20 = close_prices.rolling(window=20).mean()
            sma_50 = close_prices.rolling(window=min(50, len(close_prices))).mean()
            
            line_sma20, = self.ax_price.plot(dates, sma_20, label="SMA 20", color="#2979ff", linewidth=1.2, linestyle="--")
            line_sma50, = self.ax_price.plot(dates, sma_50, label="SMA 50", color="#ff9100", linewidth=1.2, linestyle=":")
            
            self.ax_price.set_title(f"{name} ({symbol}) Fiyat Hareketi ve 7 Günlük Tahmin", color='#ffffff', fontsize=12, fontweight='bold')
            self.ax_price.set_ylabel("Fiyat", color='#8f90a6')
            self.ax_price.tick_params(colors='#8f90a6', labelsize=9)
            self.ax_price.grid(True, color='#22232a', linestyle='-', alpha=0.5)
            
            # --- Draw ARIMA Forecast (if available) ---
            if forecast_dict:
                f_dates_str = forecast_dict.get("dates", [])
                f_dates = [pd.to_datetime(d) for d in f_dates_str]
                f_mean = forecast_dict.get("mean", [])
                f_lower = forecast_dict.get("lower", [])
                f_upper = forecast_dict.get("upper", [])
                
                if f_dates and f_mean:
                    # Stitch last historical price point to forecast start to make chart seamless
                    extended_dates = [dates[-1]] + f_dates
                    extended_mean = [close_prices.iloc[-1]] + f_mean
                    extended_lower = [close_prices.iloc[-1]] + f_lower
                    extended_upper = [close_prices.iloc[-1]] + f_upper
                    
                    # Plot dotted forecasting line in bright neon pink
                    line_f, = self.ax_price.plot(extended_dates, extended_mean, label="7G Tahmin", color="#ff1744", linewidth=2.0, linestyle=":")
                    
                    # Shade the 95% Confidence Interval band
                    self.ax_price.fill_between(
                        extended_dates, extended_lower, extended_upper,
                        color="#ff1744", alpha=0.12, label="95% Güven Aralığı"
                    )
            
            self.ax_price.legend(facecolor='#1c1c22', edgecolor='#2c2c35', loc='best', fontsize=9)
            
            # --- Draw RSI (Relative Strength Index) ---
            # Calculate RSI manually to plot it
            delta = close_prices.diff()
            gain = delta.clip(lower=0)
            loss = -delta.clip(upper=0)
            avg_gain = gain.rolling(window=14).mean()
            avg_loss = loss.rolling(window=14).mean().replace(0, 1e-9)
            rs = avg_gain / avg_loss
            rsi = 100 - (100 / (1 + rs))
            
            self.ax_rsi.plot(dates, rsi, color="#ffd600", linewidth=1.5, label="RSI-14")
            
            # Overbought / Oversold threshold lines
            self.ax_rsi.axhline(70, color="#ff1744", linestyle="--", linewidth=0.8, alpha=0.7)
            self.ax_rsi.axhline(30, color="#00e676", linestyle="--", linewidth=0.8, alpha=0.7)
            self.ax_rsi.fill_between(dates, 30, 70, color="#ffffff", alpha=0.03)  # Neutral band shading
            
            self.ax_rsi.set_ylim(0, 100)
            self.ax_rsi.set_ylabel("RSI (14)", color='#8f90a6')
            self.ax_rsi.tick_params(colors='#8f90a6', labelsize=9)
            self.ax_rsi.grid(True, color='#22232a', linestyle='-', alpha=0.5)
            
            # Formatting x-axis to be readable
            self.fig.autofmt_xdate()
            
            # Call mplcyberpunk glow effect for stunning premium look
            mplcyberpunk.add_glow_effects(ax=self.ax_price)
            
        except Exception as e:
            print("Error plotting charts:", str(e))
            traceback.print_exc()
            
        self.draw()

# --- Main Dashboard Window ---

class FinancialAnalystWindow(QMainWindow):
    """The premium visual native Desktop GUI for the Autonomous Financial Analyst System."""
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Otonom Finansal Analist ve Tahmin Sistemi")
        self.setMinimumSize(1280, 800)
        
        self.analysis_engine = AnalysisEngine()
        self.current_symbol: Optional[str] = None
        self.running_workers: Dict[str, ResearchWorker] = {}
        
        self.init_ui()
        self.load_instruments()
        self.check_ollama_status()
        
        # Periodic timer to check Ollama online status (every 10 seconds)
        self.status_timer = QTimer(self)
        self.status_timer.timeout.connect(self.check_ollama_status)
        self.status_timer.start(10000)

    def init_ui(self):
        # Global Application Stylesheet (Dark Cyberpunk Theme)
        self.setStyleSheet("""
            QMainWindow {
                background-color: #121214;
            }
            QWidget {
                background-color: #121214;
                color: #f8f8f2;
                font-family: "Segoe UI", "Arial", sans-serif;
            }
            QFrame#card {
                background-color: #1c1c22;
                border: 1px solid #2c2c35;
                border-radius: 8px;
            }
            QLabel#header_title {
                color: #ffffff;
                font-size: 18px;
                font-weight: bold;
                letter-spacing: 1px;
            }
            QListWidget {
                background-color: #1c1c22;
                border: 1px solid #2c2c35;
                border-radius: 6px;
                color: #f8f8f2;
                padding: 5px;
            }
            QListWidget::item {
                background-color: #121214;
                border-radius: 4px;
                margin: 4px 2px;
                padding: 10px;
                border: 1px solid #22232a;
            }
            QListWidget::item:selected {
                background-color: #2979ff;
                color: #ffffff;
                border: 1px solid #2979ff;
            }
            QListWidget::item:hover {
                background-color: #22232a;
            }
            QTabWidget::pane {
                border: 1px solid #2c2c35;
                background-color: #1c1c22;
                border-radius: 6px;
            }
            QTabBar::tab {
                background-color: #121214;
                color: #8f90a6;
                padding: 8px 16px;
                border-top-left-radius: 4px;
                border-top-right-radius: 4px;
                border: 1px solid #2c2c35;
                margin-right: 2px;
                font-weight: bold;
            }
            QTabBar::tab:selected {
                background-color: #1c1c22;
                color: #ffffff;
                border-bottom: 2px solid #2979ff;
            }
            QPushButton {
                background-color: #2979ff;
                color: #ffffff;
                font-weight: bold;
                border: none;
                border-radius: 4px;
                padding: 8px 16px;
            }
            QPushButton:hover {
                background-color: #2962ff;
            }
            QPushButton:pressed {
                background-color: #0039cb;
            }
            QPushButton#action_btn_danger {
                background-color: #ff1744;
            }
            QPushButton#action_btn_danger:hover {
                background-color: #d50000;
            }
            QProgressBar {
                background-color: #121214;
                border: 1px solid #2c2c35;
                border-radius: 4px;
                text-align: center;
                color: white;
                font-weight: bold;
            }
            QProgressBar::chunk {
                background-color: #00e676;
                border-radius: 3px;
            }
            QScrollBar:vertical {
                background-color: #121214;
                width: 10px;
                margin: 0px;
            }
            QScrollBar::handle:vertical {
                background-color: #2c2c35;
                min-height: 20px;
                border-radius: 5px;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0px;
            }
        """)

        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(10, 10, 10, 10)
        
        # --- Top Header Panel ---
        header_frame = QFrame()
        header_frame.setObjectName("card")
        header_frame.setFixedHeight(60)
        header_layout = QHBoxLayout(header_frame)
        header_layout.setContentsMargins(15, 0, 15, 0)
        
        title_label = QLabel("🤖 OTONOM FİNANSAL ANALİST VE TAHMİN SİSTEMİ")
        title_label.setObjectName("header_title")
        header_layout.addWidget(title_label)
        
        header_layout.addStretch()
        
        # Ollama Status Indicator
        self.ollama_status_lbl = QLabel("Ollama Durumu: Bilinmiyor ⚪")
        self.ollama_status_lbl.setStyleSheet("font-weight: bold; font-size: 13px; color: #8f90a6; margin-right: 15px;")
        header_layout.addWidget(self.ollama_status_lbl)
        
        # Model Selection Label & Dropdown
        model_lbl = QLabel("Aktif Model:")
        model_lbl.setStyleSheet("font-weight: bold; font-size: 13px; color: #f8f8f2;")
        header_layout.addWidget(model_lbl)
        
        self.model_combo = QComboBox()
        self.model_combo.setEditable(True)  # Allows direct typing (e.g. gpt-oss-120)
        self.model_combo.setFixedWidth(160)
        self.model_combo.setStyleSheet("""
            QComboBox {
                background-color: #121214;
                color: #f8f8f2;
                border: 1px solid #2c2c35;
                border-radius: 4px;
                padding: 4px;
                font-weight: bold;
            }
        """)
        # Seed default model name
        self.model_combo.addItem("Google Gemini (Bulut)")
        self.model_combo.addItem("gpt-oss:120b-cloud")
        self.model_combo.addItem("llama3")
        self.model_combo.addItem("mistral")
        header_layout.addWidget(self.model_combo)

        # Dynamic Connection & API Settings Button
        self.settings_btn = QPushButton("⚙️ Ayarlar")
        self.settings_btn.setFixedWidth(90)
        self.settings_btn.setStyleSheet("""
            QPushButton {
                background-color: #1c1c22;
                color: #f8f8f2;
                border: 1px solid #2c2c35;
                font-weight: bold;
                border-radius: 4px;
                padding: 5px;
            }
            QPushButton:hover {
                background-color: #22232a;
                border: 1px solid #2979ff;
                color: #2979ff;
            }
        """)
        self.settings_btn.clicked.connect(self.open_settings_dialog)
        header_layout.addWidget(self.settings_btn)
        
        main_layout.addWidget(header_frame)
        
        # --- Main Splitter Layout ---
        splitter = QSplitter(Qt.Orientation.Horizontal)
        
        # --- Left Panel: Instrument Tickers ---
        left_container = QWidget()
        left_layout = QVBoxLayout(left_container)
        left_layout.setContentsMargins(0, 0, 0, 0)
        
        sidebar_frame = QFrame()
        sidebar_frame.setObjectName("card")
        sidebar_layout = QVBoxLayout(sidebar_frame)
        
        # Instrument Search Box
        self.search_ticker_input = QLineEdit()
        self.search_ticker_input.setPlaceholderText("Varlık ara...")
        self.search_ticker_input.setStyleSheet("""
            QLineEdit {
                background-color: #121214;
                color: #f8f8f2;
                border: 1px solid #2c2c35;
                border-radius: 4px;
                padding: 6px;
                font-size: 13px;
            }
        """)
        self.search_ticker_input.textChanged.connect(self.filter_instruments)
        sidebar_layout.addWidget(self.search_ticker_input)
        
        # List of Tickers
        self.instruments_list = QListWidget()
        self.instruments_list.setIconSize(QSize(24, 24))
        self.instruments_list.currentItemChanged.connect(self.on_instrument_selected)
        sidebar_layout.addWidget(self.instruments_list)
        
        # Sidebar Controls
        btn_layout = QHBoxLayout()
        self.add_inst_btn = QPushButton("Sembol Ekle")
        self.add_inst_btn.setStyleSheet("background-color: #00e676; color: #121214;")
        self.add_inst_btn.clicked.connect(self.on_add_instrument_clicked)
        
        self.del_inst_btn = QPushButton("Sembol Sil")
        self.del_inst_btn.setObjectName("action_btn_danger")
        self.del_inst_btn.clicked.connect(self.on_remove_instrument_clicked)
        
        btn_layout.addWidget(self.add_inst_btn)
        btn_layout.addWidget(self.del_inst_btn)
        sidebar_layout.addLayout(btn_layout)
        
        left_layout.addWidget(sidebar_frame)
        splitter.addWidget(left_container)
        
        # --- Center Panel: Charts, Thought Logs, and Reports ---
        center_tabs = QTabWidget()
        
        # Tab 1: Quantitative Analysis (Matplotlib Chart + Logs + Report)
        tab_quant = QWidget()
        quant_layout = QHBoxLayout(tab_quant)
        quant_layout.setContentsMargins(5, 5, 5, 5)
        
        # Left Part of Quant Tab: Embedded Chart and Thought Console
        quant_left_split = QSplitter(Qt.Orientation.Vertical)
        
        # Chart Card
        chart_card = QFrame()
        chart_card.setObjectName("card")
        chart_card_layout = QVBoxLayout(chart_card)
        chart_card_layout.setContentsMargins(5, 5, 5, 5)
        
        self.canvas = MplCanvas(width=7, height=5, dpi=100)
        chart_card_layout.addWidget(self.canvas)
        quant_left_split.addWidget(chart_card)
        
        # Lower Part of Quant Tab: Sub-split between Thought Console & Economist Report
        quant_bottom_split = QSplitter(Qt.Orientation.Horizontal)
        
        # Thought and Log Flow Card
        thought_card = QFrame()
        thought_card.setObjectName("card")
        thought_layout = QVBoxLayout(thought_card)
        thought_layout.setContentsMargins(10, 10, 10, 10)
        
        thought_lbl = QLabel("🔍 AJAN DÜŞÜNCE VE CANLI LOG AKIŞI")
        thought_lbl.setStyleSheet("font-weight: bold; color: #00e676; font-size: 12px; letter-spacing: 0.5px;")
        thought_layout.addWidget(thought_lbl)
        
        self.log_console = QTextEdit()
        self.log_console.setReadOnly(True)
        # Styled like a retro matrix terminal
        self.log_console.setStyleSheet("""
            QTextEdit {
                background-color: #0b0c10;
                color: #e0e0e0;
                font-family: "Consolas", "Courier New", monospace;
                font-size: 11px;
                border: 1px solid #1a1a24;
                border-radius: 4px;
            }
        """)
        thought_layout.addWidget(self.log_console)
        
        # Log clearing button
        clear_log_btn = QPushButton("Konsolu Temizle")
        clear_log_btn.setFixedWidth(120)
        clear_log_btn.setStyleSheet("background-color: #2c2c35; font-size: 11px; padding: 4px;")
        clear_log_btn.clicked.connect(self.clear_ui_logs)
        thought_layout.addWidget(clear_log_btn)
        
        quant_bottom_split.addWidget(thought_card)
        
        # Economist Report Display Card
        report_card = QFrame()
        report_card.setObjectName("card")
        report_layout = QVBoxLayout(report_card)
        report_layout.setContentsMargins(10, 10, 10, 10)
        
        report_header_layout = QHBoxLayout()
        report_lbl = QLabel("📈 EKONOMİST RAPORU & TAHMİN RAPORU")
        report_lbl.setStyleSheet("font-weight: bold; color: #2979ff; font-size: 12px; letter-spacing: 0.5px;")
        report_header_layout.addWidget(report_lbl)
        
        report_header_layout.addStretch()
        
        self.sentiment_badge = QLabel("Sentiment: NÖTR (0.00)")
        self.sentiment_badge.setStyleSheet("font-weight: bold; color: #8f90a6; font-size: 11px; background-color: #121214; padding: 3px 8px; border-radius: 4px;")
        report_header_layout.addWidget(self.sentiment_badge)
        
        # Export Report Button
        self.export_report_btn = QPushButton("📄 Raporu Aktar")
        self.export_report_btn.setStyleSheet("""
            QPushButton {
                background-color: #2c2c35;
                color: #ffffff;
                font-weight: bold;
                font-size: 11px;
                padding: 3px 8px;
                border-radius: 4px;
                border: none;
            }
            QPushButton:hover {
                background-color: #3e3f4b;
            }
        """)
        self.export_report_btn.clicked.connect(self.export_report_to_file)
        report_header_layout.addWidget(self.export_report_btn)
        
        report_layout.addLayout(report_header_layout)
        
        self.report_browser = QTextBrowser()
        # Open links in standard external browser
        self.report_browser.setOpenExternalLinks(True)
        self.report_browser.setStyleSheet("""
            QTextBrowser {
                background-color: #121214;
                color: #f8f8f2;
                border: 1px solid #2b2b36;
                border-radius: 4px;
                padding: 10px;
                font-size: 13px;
                line-height: 1.4;
            }
        """)
        report_layout.addWidget(self.report_browser)
        quant_bottom_split.addWidget(report_card)
        
        quant_left_split.addWidget(quant_bottom_split)
        
        # Set sizing ratios: Chart gets 60% of vertical, logs/reports split get 40%
        quant_left_split.setSizes([450, 300])
        quant_bottom_split.setSizes([350, 450])
        
        quant_layout.addWidget(quant_left_split)
        center_tabs.addTab(tab_quant, "Kantitatif ve Zeka Analizi")
        
        # Tab 2: Forecast Stat Table Tab
        tab_forecast = QWidget()
        forecast_layout = QVBoxLayout(tab_forecast)
        forecast_layout.setContentsMargins(10, 10, 10, 10)
        
        forecast_card = QFrame()
        forecast_card.setObjectName("card")
        forecast_card_layout = QVBoxLayout(forecast_card)
        
        forecast_title = QLabel("📊 7 GÜNLÜK KANTİTATİF TAHMİN PROJEKSİYONLARI")
        forecast_title.setStyleSheet("font-weight: bold; font-size: 14px; color: #00e676; margin-bottom: 5px;")
        forecast_card_layout.addWidget(forecast_title)
        
        forecast_desc = QLabel("ARIMA istatistiksel zaman serisi modeli tarafından hesaplanan tahmin fiyatları ve 95% güven aralığı destek/direnç bantları.")
        forecast_desc.setStyleSheet("color: #8f90a6; font-size: 12px; margin-bottom: 12px;")
        forecast_card_layout.addWidget(forecast_desc)
        
        # Table Widget
        from PyQt6.QtWidgets import QTableWidget, QTableWidgetItem, QHeaderView
        self.forecast_table = QTableWidget()
        self.forecast_table.setColumnCount(5)
        self.forecast_table.setHorizontalHeaderLabels([
            "Öngörülen Tarih", 
            "Tahmini Fiyat", 
            "Alt Limit (Destek)", 
            "Üst Limit (Direnç)", 
            "Beklenen Yön / Değişim"
        ])
        self.forecast_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.forecast_table.setStyleSheet("""
            QTableWidget {
                background-color: #121214;
                gridline-color: #2c2c35;
                color: #f8f8f2;
                border: 1px solid #2c2c35;
                border-radius: 4px;
            }
            QHeaderView::section {
                background-color: #1c1c22;
                color: #2979ff;
                padding: 8px;
                font-weight: bold;
                border: 1px solid #2c2c35;
            }
        """)
        forecast_card_layout.addWidget(self.forecast_table)
        
        forecast_layout.addWidget(forecast_card)
        center_tabs.addTab(tab_forecast, "Tahmin Detayları")
        
        # Tab 3: Semantic RAG Search Tab (Article Database)
        tab_rag = QWidget()
        rag_layout = QVBoxLayout(tab_rag)
        rag_layout.setContentsMargins(10, 10, 10, 10)
        
        rag_card = QFrame()
        rag_card.setObjectName("card")
        rag_card_layout = QVBoxLayout(rag_card)
        
        rag_desc = QLabel("📚 KAZINAN HABER HAVUZU VE YEREL RAG ARAMA")
        rag_desc.setStyleSheet("font-weight: bold; font-size: 14px; color: #ffd600;")
        rag_card_layout.addWidget(rag_desc)
        
        rag_subdesc = QLabel("Sistem tarafından otonom kazınan tüm makaleleri anlamsal olarak tarayın (TF-IDF Vektör Tabanlı).")
        rag_subdesc.setStyleSheet("color: #8f90a6; font-size: 12px; margin-bottom: 10px;")
        rag_card_layout.addWidget(rag_subdesc)
        
        # Search Box layout
        search_layout = QHBoxLayout()
        self.rag_search_input = QLineEdit()
        self.rag_search_input.setPlaceholderText("Haberler arasında anlamsal arama yapın... Örn: Fed faiz kararı etkileri, OPEC petrol kesintileri")
        self.rag_search_input.setStyleSheet("""
            QLineEdit {
                background-color: #121214;
                color: #f8f8f2;
                border: 1px solid #2c2c35;
                border-radius: 4px;
                padding: 8px;
                font-size: 13px;
            }
        """)
        self.rag_search_input.returnPressed.connect(self.on_rag_search_clicked)
        
        self.rag_search_btn = QPushButton("Anlamsal Ara")
        self.rag_search_btn.setStyleSheet("background-color: #ffd600; color: #121214;")
        self.rag_search_btn.clicked.connect(self.on_rag_search_clicked)
        
        search_layout.addWidget(self.rag_search_input)
        search_layout.addWidget(self.rag_search_btn)
        rag_card_layout.addLayout(search_layout)
        
        # Search results list
        self.rag_results_list = QListWidget()
        self.rag_results_list.setStyleSheet("""
            QListWidget {
                background-color: #121214;
                border: 1px solid #2c2c35;
                border-radius: 6px;
            }
        """)
        self.rag_results_list.itemDoubleClicked.connect(self.on_rag_item_double_clicked)
        rag_card_layout.addWidget(self.rag_results_list)
        
        # Show all articles button
        self.show_all_articles_btn = QPushButton("Tüm Makaleleri Listele")
        self.show_all_articles_btn.setStyleSheet("background-color: #2c2c35; color: #ffffff;")
        self.show_all_articles_btn.clicked.connect(self.load_all_articles_in_rag)
        rag_card_layout.addWidget(self.show_all_articles_btn)
        
        rag_layout.addWidget(rag_card)
        center_tabs.addTab(tab_rag, "Haber Deposu & Yerel RAG")
        
        splitter.addWidget(center_tabs)
        
        # Set splitter sizes: Sidebar gets 20%, Center widgets get 80%
        splitter.setSizes([260, 1020])
        main_layout.addWidget(splitter)
        
        # --- Bottom Status bar and Progress Panel ---
        status_panel = QFrame()
        status_panel.setObjectName("card")
        status_panel.setFixedHeight(45)
        status_layout = QHBoxLayout(status_panel)
        status_layout.setContentsMargins(15, 0, 15, 0)
        
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setFormat("Ajan Durumu: Hazır")
        self.progress_bar.setFixedHeight(22)
        status_layout.addWidget(self.progress_bar)
        
        self.research_selected_btn = QPushButton("Seçileni Araştır")
        self.research_selected_btn.setStyleSheet("background-color: #2979ff; color: white;")
        self.research_selected_btn.clicked.connect(self.on_research_selected_clicked)
        status_layout.addWidget(self.research_selected_btn)
        
        self.research_all_btn = QPushButton("Tümünü Güncelle")
        self.research_all_btn.setStyleSheet("background-color: #3e3f4b; color: white;")
        self.research_all_btn.clicked.connect(self.on_research_all_clicked)
        status_layout.addWidget(self.research_all_btn)
        
        main_layout.addWidget(status_panel)

    def open_settings_dialog(self):
        """Opens the API and connection settings popup dialog."""
        dialog = SettingsDialog(self)
        dialog.exec()

    # --- Database & List Loaders ---

    def load_instruments(self):
        """Loads active instruments from the SQLite database into the sidebar list."""
        self.instruments_list.clear()
        try:
            instruments = database.get_active_instruments()
            for inst in instruments:
                symbol = inst["symbol"]
                name = inst["name"]
                category = inst["category"]
                
                # Retrieve price change percentage from database if available
                change_str = "--"
                color_str = "#8f90a6"
                
                prices = database.get_prices(symbol)
                if not prices.empty and len(prices) >= 2:
                    last_price = prices['Close'].iloc[-1]
                    prev_price = prices['Close'].iloc[-2]
                    change_pct = ((last_price - prev_price) / prev_price) * 100
                    change_str = f"{change_pct:+.2f}%"
                    color_str = "#00e676" if change_pct >= 0 else "#ff1744"
                
                # Set up elegant multi-line display string
                disp_text = f"{name} ({symbol})\n{category}  •  {change_str}"
                
                item = QListWidgetItem()
                item.setText(disp_text)
                item.setData(Qt.ItemDataRole.UserRole, symbol)
                item.setData(Qt.ItemDataRole.ToolTipRole, f"{name} - {category}")
                
                # Color code indicator based on positive or negative daily change
                item.setForeground(QColor(color_str))
                
                self.instruments_list.addItem(item)
                
            # Automatically select the first item if list is not empty
            if self.instruments_list.count() > 0:
                self.instruments_list.setCurrentRow(0)
        except Exception as e:
            QMessageBox.critical(self, "Sembol Yükleme Hatası", f"Veritabanından enstrümanlar çekilemedi: {str(e)}")

    def filter_instruments(self):
        """Filters instrument sidebar items dynamically based on search box entry."""
        search_text = self.search_ticker_input.text().lower()
        for i in range(self.instruments_list.count()):
            item = self.instruments_list.item(i)
            item_text = item.text().lower()
            item.setHidden(search_text not in item_text)

    # --- UI Interactions & Selection ---

    def on_instrument_selected(self, current: Optional[QListWidgetItem], previous: Optional[QListWidgetItem]):
        """Triggered when user clicks a different instrument in the sidebar list."""
        if not current:
            return
            
        symbol = current.data(Qt.ItemDataRole.UserRole)
        self.current_symbol = symbol
        
        # Load instrument details
        instruments = database.get_active_instruments()
        inst_dict = next((i for i in instruments if i["symbol"] == symbol), None)
        name = inst_dict["name"] if inst_dict else symbol
        
        # Load cached historical prices
        df = database.get_prices(symbol)
        
        # Load cached forecast data from latest analysis report
        report_data = database.get_report(symbol)
        
        forecast_dict = None
        report_content = "### Araştırma Bulunamadı\n\nBu enstrüman henüz otonom ajan tarafından araştırılmamıştır. Analizleri başlatmak için lütfen **'Seçileni Araştır'** butonuna tıklayın."
        sentiment_text = "Sentiment: NÖTR (0.00)"
        sentiment_color = "#8f90a6"
        
        if report_data:
            report_content = report_data.get("report_content", "")
            sentiment_val = report_data.get("sentiment_score", 0.0)
            
            # Formatting sentiment badge
            if sentiment_val > 0.15:
                sentiment_text = f"Sentiment: POZİTİF ({sentiment_val:+.2f})"
                sentiment_color = "#00e676"
            elif sentiment_val < -0.15:
                sentiment_text = f"Sentiment: NEGATİF ({sentiment_val:+.2f})"
                sentiment_color = "#ff1744"
            else:
                sentiment_text = f"Sentiment: NÖTR ({sentiment_val:+.2f})"
                sentiment_color = "#ffd600"
                
            try:
                forecast_dict = json.loads(report_data.get("forecast_json", "{}"))
            except Exception:
                pass

        # Update visual controls
        self.sentiment_badge.setText(sentiment_text)
        self.sentiment_badge.setStyleSheet(f"font-weight: bold; color: {sentiment_color}; font-size: 11px; background-color: #121214; padding: 3px 8px; border-radius: 4px;")
        
        # Render Markdown report
        self.report_browser.setMarkdown(report_content)
        
        # Redraw glowing Matplotlib chart
        self.canvas.plot_data(symbol, name, df, forecast_dict)
        
        # Populate the 7-day forecast table
        current_close = float(df['Close'].iloc[-1]) if not df.empty else 0.0
        self.populate_forecast_table(forecast_dict, current_close)
        
        # Load historical logs specific to this asset
        self.load_local_logs(symbol)

    def load_local_logs(self, symbol: str):
        """Loads logs from SQLite to show historical agent progression for this asset."""
        self.log_console.clear()
        logs = database.get_logs(limit=80)
        
        for log in logs:
            msg = log["message"]
            # Only display general logs or logs matching the active symbol
            if symbol in msg or "System" in log["agent"] or not self.current_symbol:
                self.append_log_to_console(log["timestamp"], log["agent"], log["level"], msg)

    def append_log_to_console(self, timestamp: str, agent: str, level: str, message: str):
        """Streams formatted log messages with stylized colored headers matching the active agent."""
        cursor = self.log_console.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self.log_console.setTextCursor(cursor)
        
        # Color palettes for agent tags
        agent_colors = {
            "Bridge Agent": "#00e676",      # Neon green
            "Fast Scraper": "#ff9100",      # Neon orange
            "Quantitative Engine": "#d500f9",# Hot purple
            "Analyst LLM": "#2979ff",       # Electric blue
            "System": "#8f90a6",            # Slate gray
            "Database": "#ffd600"           # Amber
        }
        
        color = agent_colors.get(agent, "#ffffff")
        
        time_part = f"[{timestamp}] "
        agent_part = f"[{agent}] "
        level_part = f"[{level}] "
        
        # Insert styled html
        html_msg = f"<span style='color: #8f90a6;'>{time_part}</span>"
        html_msg += f"<span style='color: {color}; font-weight: bold;'>{agent_part}</span>"
        html_msg += f"<span style='color: {'#ff1744' if level == 'ERROR' else '#ffd600' if level == 'WARNING' else '#8f90a6'};'>{level_part}</span>"
        html_msg += f"<span style='color: #f8f8f2;'>{message}</span><br>"
        
        self.log_console.insertHtml(html_msg)
        
        # Scroll to bottom
        self.log_console.ensureCursorVisible()

    def clear_ui_logs(self):
        """Clears console and log table in DB."""
        self.log_console.clear()
        database.clear_logs()
        self.log_console.append(">> Konsol temizlendi. Ajan günlükleri sıfırlandı.")

    def populate_forecast_table(self, forecast_dict: Optional[Dict[str, Any]], current_price: float):
        """Populates the 7-day ARIMA forecast table with styled cell values."""
        self.forecast_table.setRowCount(0)
        if not forecast_dict:
            return
            
        dates = forecast_dict.get("dates", [])
        mean = forecast_dict.get("mean", [])
        lower = forecast_dict.get("lower", [])
        upper = forecast_dict.get("upper", [])
        
        self.forecast_table.setRowCount(len(dates))
        
        prev_val = current_price
        
        for idx in range(len(dates)):
            date_val = dates[idx]
            mean_val = mean[idx]
            lower_val = lower[idx]
            upper_val = upper[idx]
            
            # Expected change from current price (first day) or previous forecasted day
            change_pct = 0.0
            if prev_val > 0:
                change_pct = ((mean_val - prev_val) / prev_val) * 100
            prev_val = mean_val
            
            # 1. Date Item
            date_item = QTableWidgetItem(str(date_val))
            date_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            
            # 2. Mean Item
            mean_item = QTableWidgetItem(f"{mean_val:.4f}")
            mean_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            mean_item.setForeground(QColor("#ffffff"))
            
            # 3. Lower Item
            lower_item = QTableWidgetItem(f"{lower_val:.4f}")
            lower_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            lower_item.setForeground(QColor("#ff1744")) # Support in red
            
            # 4. Upper Item
            upper_item = QTableWidgetItem(f"{upper_val:.4f}")
            upper_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            upper_item.setForeground(QColor("#00e676")) # Resistance in green
            
            # 5. Change Item
            change_item = QTableWidgetItem(f"{change_pct:+.2f}%")
            change_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            change_color = "#00e676" if change_pct >= 0 else "#ff1744"
            change_item.setForeground(QColor(change_color))
            
            self.forecast_table.setItem(idx, 0, date_item)
            self.forecast_table.setItem(idx, 1, mean_item)
            self.forecast_table.setItem(idx, 2, lower_item)
            self.forecast_table.setItem(idx, 3, upper_item)
            self.forecast_table.setItem(idx, 4, change_item)

    def export_report_to_file(self):
        """Exports the active report to a user-selected location (supports Markdown and PDF)."""
        if not self.current_symbol:
            QMessageBox.warning(self, "Sembol Seçilmedi", "Dışa aktarmak için geçerli bir rapor bulunmamaktadır.")
            return
            
        report_data = database.get_report(self.current_symbol)
        if not report_data or not report_data.get("report_content"):
            QMessageBox.warning(self, "Rapor Yok", "Bu sembol için henüz bir rapor oluşturulmamış.")
            return
            
        report_content = report_data["report_content"]
        
        from PyQt6.QtWidgets import QFileDialog
        
        # Suggest filename based on symbol
        default_name = f"{self.current_symbol.replace('=', '').replace('^', '')}_Analiz_Raporu"
        
        file_path, selected_filter = QFileDialog.getSaveFileName(
            self, 
            "Raporu Kaydet", 
            default_name, 
            "PDF Belgesi (*.pdf);;Markdown Dosyası (*.md);;Tüm Dosyalar (*)"
        )
        
        if file_path:
            # Determine format based on file extension or selected filter
            is_pdf = file_path.lower().endswith(".pdf") or "pdf" in selected_filter.lower()
            
            # Ensure correct extension is appended
            if is_pdf and not file_path.lower().endswith(".pdf"):
                file_path += ".pdf"
            elif not is_pdf and not file_path.lower().endswith(".md") and not file_path.lower().endswith(".txt"):
                file_path += ".md"
                
            try:
                if is_pdf:
                    # Export as PDF using Qt's native QPdfWriter
                    from PyQt6.QtGui import QPdfWriter, QTextDocument, QPageLayout, QPageSize
                    from PyQt6.QtCore import QMarginsF
                    
                    writer = QPdfWriter(file_path)
                    writer.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
                    writer.setPageMargins(QMarginsF(15, 15, 15, 15), QPageLayout.Unit.Millimeter)
                    
                    doc = QTextDocument()
                    doc.setMarkdown(report_content)
                    doc.print(writer)
                    
                    QMessageBox.information(self, "Başarılı", f"PDF raporu başarıyla kaydedildi:\n{os.path.basename(file_path)}")
                else:
                    # Export as Markdown raw text
                    with open(file_path, "w", encoding="utf-8") as f:
                        f.write(report_content)
                    QMessageBox.information(self, "Başarılı", f"Markdown raporu başarıyla kaydedildi:\n{os.path.basename(file_path)}")
            except Exception as e:
                QMessageBox.critical(self, "Hata", f"Rapor kaydedilirken hata oluştu:\n{str(e)}")

    # --- Ollama Connection Health Probing ---

    def check_ollama_status(self):
        """Checks if local Ollama server is online and discovers installed models."""
        # Thread guard to prevent accumulating multiple concurrent background check threads
        if hasattr(self, 'status_worker') and self.status_worker.isRunning():
            return
        class StatusWorker(QThread):
            status_signal = pyqtSignal(bool, list)
            def __init__(self, engine: AnalysisEngine) -> None:
                super().__init__()
                self.engine = engine
            def run(self):
                import asyncio
                try:
                    models = asyncio.run(self.engine.get_ollama_models())
                    if models is not None:
                        self.status_signal.emit(True, models)
                    else:
                        self.status_signal.emit(False, [])
                except Exception:
                    self.status_signal.emit(False, [])

        self.status_worker = StatusWorker(self.analysis_engine)
        
        def handle_status(is_online: bool, models: List[str]):
            if not hasattr(self, 'finished_workers_pool'):
                self.finished_workers_pool = []
            self.finished_workers_pool.append(self.status_worker)
            
            if is_online:
                self.ollama_status_lbl.setText("Ollama Durumu: ÇEVRİMİÇİ 🟢")
                self.ollama_status_lbl.setStyleSheet("font-weight: bold; font-size: 13px; color: #00e676; margin-right: 15px;")
                
                # Update Combobox if new models found
                current_items = [self.model_combo.itemText(i) for i in range(self.model_combo.count())]
                for m in models:
                    if m not in current_items:
                        self.model_combo.addItem(m)
            else:
                self.ollama_status_lbl.setText("Ollama Durumu: ÇEVRİMDIŞI 🔴")
                self.ollama_status_lbl.setStyleSheet("font-weight: bold; font-size: 13px; color: #ff1744; margin-right: 15px;")
                
        self.status_worker.status_signal.connect(handle_status)
        self.status_worker.start()

    # --- Instrument Add/Remove Logic ---

    def on_add_instrument_clicked(self):
        """Opens popup dialog to dynamically append a new ticker to the SQLite DB."""
        dialog = AddInstrumentDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            data = dialog.get_data()
            symbol = data["symbol"].upper()
            name = data["name"]
            category = data["category"]
            
            if not symbol or not name:
                QMessageBox.warning(self, "Eksik Bilgi", "Sembol ve Görünen Ad alanları boş bırakılamaz.")
                return
                
            success = database.add_instrument(symbol, name, category)
            if success:
                QMessageBox.information(self, "Başarılı", f"'{name} ({symbol})' başarıyla listenize eklendi.")
                self.load_instruments()
            else:
                QMessageBox.critical(self, "Hata", "Enstrüman eklenirken veritabanı hatası oluştu.")

    def on_remove_instrument_clicked(self):
        """Deletes selected instrument from system."""
        if not self.current_symbol:
            QMessageBox.warning(self, "Seçim Yapılmadı", "Lütfen silmek istediğiniz enstrümanı listeden seçin.")
            return
            
        reply = QMessageBox.question(
            self, "Onay",
            f"'{self.current_symbol}' sembolünü ve tüm fiyat/analiz geçmişini kalıcı olarak silmek istediğinize emin misiniz?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        
        if reply == QMessageBox.StandardButton.Yes:
            success = database.remove_instrument(self.current_symbol)
            if success:
                self.current_symbol = None
                self.load_instruments()
            else:
                QMessageBox.critical(self, "Hata", "Sembol veritabanından silinemedi.")

    # --- Execution Workers & Progression ---

    def on_research_progress(self, percent: int, msg: str):
        """Thread-safe UI progress update slot. Executed on the GUI thread."""
        self.progress_bar.setValue(percent)
        self.progress_bar.setFormat(f"Araştırılıyor: {percent}% - {msg}")

    def on_research_finished(self, success: bool, symbol: str, message: str):
        """Thread-safe UI complete update slot. Executed on the GUI thread."""
        # Clean worker reference but keep it alive to prevent Python 3.13 dummy thread GC warnings in VS Code Debugger
        if symbol in self.running_workers:
            worker = self.running_workers.pop(symbol)
            if not hasattr(self, 'finished_workers_pool'):
                self.finished_workers_pool = []
            self.finished_workers_pool.append(worker)
            
        self.progress_bar.setValue(100 if success else 0)
        self.progress_bar.setFormat("Ajan Durumu: Hazır")
        
        if success:
            # Reload sidebar to show newly updated daily change percentages
            self.load_instruments()
            # Find and re-select the symbol in the list to reload plots & reports
            for idx in range(self.instruments_list.count()):
                item = self.instruments_list.item(idx)
                if item.data(Qt.ItemDataRole.UserRole) == symbol:
                    self.instruments_list.setCurrentItem(item)
                    break
        else:
            QMessageBox.critical(self, "Araştırma Hatası", f"'{symbol}' araştırması sırasında kritik bir hata oluştu:\n{message}")

    def on_research_selected_clicked(self):
        """Starts background worker to perform autonomous research cycle for the selected instrument."""
        if not self.current_symbol:
            QMessageBox.warning(self, "Seçim Yapılmadı", "Lütfen araştırmak istediğiniz enstrümanı seçin.")
            return
            
        symbol = self.current_symbol
        
        # Check if already running to prevent duplicate threads
        if symbol in self.running_workers:
            QMessageBox.warning(self, "Zaten Çalışıyor", f"'{symbol}' araştırması şu anda arka planda zaten yürütülmektedir.")
            return
            
        # Retrieve selected Ollama/Cloud model
        model_name = self.model_combo.currentText().strip()
        if not model_name:
            model_name = "gpt-oss:120b-cloud"
            
        instruments = database.get_active_instruments()
        inst_dict = next((i for i in instruments if i["symbol"] == symbol), None)
        name = inst_dict["name"] if inst_dict else symbol
        
        # Initialize background QThread
        worker = ResearchWorker(symbol, name, model_name)
        self.running_workers[symbol] = worker
        
        # Connect Signals to GUI Slots (guaranteed to execute safely on the GUI thread)
        worker.log_signal.connect(self.append_log_to_console)
        worker.progress_signal.connect(self.on_research_progress)
        worker.finished_signal.connect(self.on_research_finished)
        
        # Start background thread execution
        worker.start()

    def on_research_all_clicked(self):
        """Iterates through all active instruments and chains background research workers."""
        instruments = database.get_active_instruments()
        if not instruments:
            return
            
        # Chain execution using sequential timer to avoid overloading connection pools
        self.all_symbols_to_research = [i["symbol"] for i in instruments]
        self.all_research_idx = 0
        
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("Toplu Araştırma Başlatılıyor...")
        
        # Set up a small interval timer to trigger next agent sequence
        self.chain_timer = QTimer(self)
        
        def trigger_next_chain():
            if self.all_research_idx >= len(self.all_symbols_to_research):
                self.chain_timer.stop()
                self.progress_bar.setValue(100)
                self.progress_bar.setFormat("Tüm Enstrümanlar Güncellendi!")
                return
                
            sym = self.all_symbols_to_research[self.all_research_idx]
            
            # Select symbol in sidebar to show active plot
            for idx in range(self.instruments_list.count()):
                item = self.instruments_list.item(idx)
                if item.data(Qt.ItemDataRole.UserRole) == sym:
                    self.instruments_list.setCurrentItem(item)
                    break
                    
            # Trigger research
            self.on_research_selected_clicked()
            self.all_research_idx += 1
            
        self.chain_timer.timeout.connect(trigger_next_chain)
        # Trigger next instrument research every 1.5 seconds if preceding one completed
        self.chain_timer.start(1500)

    # --- Semantic RAG Search Tab Interaction ---

    def on_rag_search_clicked(self):
        """Invokes LightweightVectorStore TF-IDF Cosine Similarity semantic search on scraped articles."""
        query = self.rag_search_input.text().strip()
        if not query:
            self.load_all_articles_in_rag()
            return
            
        self.rag_results_list.clear()
        
        vector_store = LightweightVectorStore()
        # Search all active instruments combined
        results = vector_store.search(query, symbol=None, top_k=20)
        
        if not results:
            self.rag_results_list.addItem("Aramanızla eşleşen hiçbir kayıt bulunamadı.")
            return
            
        for i, (art, score) in enumerate(results):
            title = art.get("title", "Başlıksız")
            source = art.get("source", "Bilinmeyen Kaynak")
            pub_date = art.get("pub_date", "Tarihsiz")
            symbol = art.get("symbol", "GENEL")
            sentiment = art.get("sentiment", "NEUTRAL")
            
            # Build informative styled listing
            disp_text = (
                f"[{i+1}] {title}\n"
                f"Kategori: {symbol}  •  Kaynak: {source}  •  Tarih: {pub_date}\n"
                f"Duyarlılık: {sentiment}  •  Eşleşme Skoru: {score:.4f}"
            )
            
            item = QListWidgetItem()
            item.setText(disp_text)
            item.setData(Qt.ItemDataRole.UserRole, art.get("url")) # URL is primary key in DB
            
            # Sentiment coloring
            if sentiment == "BULLISH":
                item.setForeground(QColor("#00e676"))
            elif sentiment == "BEARISH":
                item.setForeground(QColor("#ff1744"))
            else:
                item.setForeground(QColor("#ffd600"))
                
            self.rag_results_list.addItem(item)

    def load_all_articles_in_rag(self):
        """Displays all scraped articles ordered by database creation time."""
        self.rag_results_list.clear()
        articles = database.get_articles()
        
        if not articles:
            self.rag_results_list.addItem("Yerel makale deposu şu anda boş. Ajanları çalıştırarak haber toplayın!")
            return
            
        for i, art in enumerate(articles):
            title = art.get("title", "Başlıksız")
            source = urllib.parse.urlparse(art.get("url")).netloc if art.get("url") else "Kaynak"
            pub_date = art.get("pub_date", "Tarihsiz")
            symbol = art.get("symbol", "GENEL")
            sentiment = art.get("sentiment", "NEUTRAL")
            
            disp_text = (
                f"[{i+1}] {title}\n"
                f"Kategori: {symbol}  •  Kaynak: {source}  •  Tarih: {pub_date}\n"
                f"Duyarlılık: {sentiment}"
            )
            
            item = QListWidgetItem()
            item.setText(disp_text)
            item.setData(Qt.ItemDataRole.UserRole, art.get("url"))
            
            if sentiment == "BULLISH":
                item.setForeground(QColor("#00e676"))
            elif sentiment == "BEARISH":
                item.setForeground(QColor("#ff1744"))
            else:
                item.setForeground(QColor("#ffd600"))
                
            self.rag_results_list.addItem(item)

    def on_rag_item_double_clicked(self, item: QListWidgetItem):
        """Opens a beautiful custom popup dialog showcasing the entire scraped text and metadata of selected article."""
        url = item.data(Qt.ItemDataRole.UserRole)
        if not url:
            return
            
        # Fetch detailed article from database
        articles = database.get_articles()
        art = next((a for a in articles if a["url"] == url), None)
        if not art:
            return
            
        # Create Popup Dialog
        popup = QDialog(self)
        popup.setWindowTitle(art.get("title", "Haber Detayı"))
        popup.setMinimumSize(700, 500)
        popup.setStyleSheet("""
            QDialog {
                background-color: #1c1c22;
                color: #f8f8f2;
                border: 1px solid #2c2c35;
            }
            QLabel {
                color: #f8f8f2;
            }
            QTextBrowser {
                background-color: #121214;
                color: #f8f8f2;
                border: 1px solid #2c2c35;
                padding: 15px;
                font-size: 13px;
                line-height: 1.5;
            }
            QPushButton {
                background-color: #2979ff;
                color: white;
                font-weight: bold;
                border-radius: 4px;
                padding: 8px;
                border: none;
            }
            QPushButton:hover {
                background-color: #2962ff;
            }
        """)
        
        layout = QVBoxLayout(popup)
        
        title_lbl = QLabel(art.get("title", "Başlıksız"))
        title_lbl.setStyleSheet("font-size: 16px; font-weight: bold; color: #ffffff;")
        title_lbl.setWordWrap(True)
        layout.addWidget(title_lbl)
        
        meta_lbl = QLabel(f"Yazar: {art.get('author', 'Bilinmeyen')}  •  Kaynak: {art.get('url', 'Bilinmeyen')[:60]}...  •  Tarih: {art.get('pub_date')}")
        meta_lbl.setStyleSheet("color: #8f90a6; font-size: 11px;")
        meta_lbl.setWordWrap(True)
        layout.addWidget(meta_lbl)
        
        layout.addSpacing(10)
        
        browser = QTextBrowser()
        # Clean displays
        content = art.get("content", "Metin boş veya kazınamadı.")
        browser.setText(content)
        layout.addWidget(browser)
        
        ctrl_layout = QHBoxLayout()
        # Button to open original URL in local browser
        url_btn = QPushButton("Orijinal Kaynağa Git (Web)")
        url_btn.clicked.connect(lambda: webbrowser.open(url))
        ctrl_layout.addWidget(url_btn)
        
        close_btn = QPushButton("Kapat")
        close_btn.setStyleSheet("background-color: #3e3f4b;")
        close_btn.clicked.connect(popup.accept)
        ctrl_layout.addWidget(close_btn)
        
        layout.addLayout(ctrl_layout)
        
        popup.exec()

# --- Main Entry Point ---

def main():
    # Force high DPI scaling support
    os.environ["QT_AUTO_SCREEN_SCALE_FACTOR"] = "1"
    
    # Suppress harmless database resource warnings and package rename warnings
    import warnings
    warnings.filterwarnings("ignore", category=ResourceWarning)
    warnings.filterwarnings("ignore", category=RuntimeWarning)
    
    # Configure thread-safe and crash-free asyncio event loop policy on Windows
    if sys.platform == "win32":
        import asyncio
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    
    # Initialize SQLite Database tables
    database.init_db()
    
    app = QApplication(sys.argv)
    
    # Apply anti-aliasing / font rendering overrides on Windows
    font = QFont("Segoe UI", 9)
    app.setFont(font)
    
    # Check if user previously opted out of the splash screen
    skip_splash = database.get_setting("skip_splash", "false") == "true"
    
    if not skip_splash:
        from splash_screen import SplashScreen
        splash = SplashScreen()
        
        def on_splash_finished():
            """Called when splash animation completes — launch the main dashboard."""
            window = FinancialAnalystWindow()
            window.show()
            app._main_window = window   # prevent garbage collection
        
        splash.finished.connect(on_splash_finished)
        splash.show()
        
        # Center splash on the primary display
        screen_geo = app.primaryScreen().availableGeometry()
        splash.move(
            (screen_geo.width() - splash.width()) // 2,
            (screen_geo.height() - splash.height()) // 2,
        )
        app._splash = splash   # prevent garbage collection
    else:
        window = FinancialAnalystWindow()
        window.show()
    
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
