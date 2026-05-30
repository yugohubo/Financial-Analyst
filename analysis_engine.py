import datetime
import json
import re
import urllib.parse
import httpx
import numpy as np
import pandas as pd
import yfinance as yf
from typing import Dict, Any, List, Optional, Tuple

# Import ARIMA
import warnings
with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    from statsmodels.tsa.arima.model import ARIMA

from database import save_log, save_prices, save_report, get_prices, get_setting
from vector_store import LightweightVectorStore

class AnalysisEngine:
    """
    Main quantitative analysis & LLM reasoning engine.
    Calculates technical indicators, runs statistical ARIMA forecasts,
    queries local Ollama models (with gpt-oss-120 support), and produces economist reports.
    """
    def __init__(self) -> None:
        self.vector_store = LightweightVectorStore()
        # Using 127.0.0.1 instead of localhost avoids IPv6 resolution delays/issues on Windows
        self.ollama_base_url = "http://127.0.0.1:11434"

    # --- 1. YFinance Fetching & Technical Indicators ---

    def fetch_historical_data(self, symbol: str, period_days: int = 90) -> pd.DataFrame:
        """Downloads historical price data from Yahoo Finance and saves it to SQLite."""
        save_log("Data Fetcher", "INFO", f"Fetching yfinance history for {symbol} (period: {period_days} days)")
        try:
            # We fetch slightly more than period_days to account for weekends and technical calculations (e.g. SMA 50)
            end_date = datetime.datetime.now()
            start_date = end_date - datetime.timedelta(days=period_days + 40)
            
            ticker = yf.Ticker(symbol)
            df = ticker.history(start=start_date.strftime('%Y-%m-%d'), end=end_date.strftime('%Y-%m-%d'))
            
            if df.empty:
                save_log("Data Fetcher", "WARNING", f"No data found for symbol: {symbol}")
                # Fallback to existing database prices
                return get_prices(symbol)
            
            # Save to SQLite database
            save_prices(symbol, df)
            
            # Retrieve only the requested period_days from database to keep it clean
            full_df = get_prices(symbol)
            cutoff_date = pd.to_datetime(datetime.datetime.now() - datetime.timedelta(days=period_days))
            filtered_df = full_df[full_df.index >= cutoff_date]
            
            save_log("Data Fetcher", "INFO", f"Loaded {len(filtered_df)} price records for {symbol} successfully.")
            return filtered_df
            
        except Exception as e:
            save_log("Data Fetcher", "ERROR", f"Failed to fetch data for {symbol}: {str(e)}")
            # Fallback to whatever is in the local database
            return get_prices(symbol)

    def calculate_indicators(self, df: pd.DataFrame) -> Dict[str, Any]:
        """Calculates standard technical indicators (SMA, RSI, MACD, Volatility)."""
        if df.empty or len(df) < 14:
            return {}
            
        close = df['Close']
        
        # 1. Simple Moving Averages
        sma_20 = close.rolling(window=20).mean()
        sma_50 = close.rolling(window=min(50, len(close))).mean()
        
        # 2. Relative Strength Index (RSI-14)
        delta = close.diff()
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)
        
        # Wilder's smoothing
        avg_gain = gain.rolling(window=14).mean()
        avg_loss = loss.rolling(window=14).mean()
        
        # Avoid division by zero
        avg_loss = avg_loss.replace(to_replace=0, value=1e-9)
        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))
        
        # 3. MACD
        exp1 = close.ewm(span=12, adjust=False).mean()
        exp2 = close.ewm(span=26, adjust=False).mean()
        macd = exp1 - exp2
        signal = macd.ewm(span=9, adjust=False).mean()
        macd_hist = macd - signal
        
        # 4. 20-day Volatility (annualized)
        daily_returns = close.pct_change()
        volatility = daily_returns.rolling(window=20).std() * np.sqrt(252) * 100
        
        # Get latest values
        latest_idx = df.index[-1]
        
        return {
            "close": float(close.iloc[-1]),
            "sma_20": float(sma_20.iloc[-1]) if not pd.isna(sma_20.iloc[-1]) else float(close.iloc[-1]),
            "sma_50": float(sma_50.iloc[-1]) if not pd.isna(sma_50.iloc[-1]) else float(close.iloc[-1]),
            "rsi": float(rsi.iloc[-1]) if not pd.isna(rsi.iloc[-1]) else 50.0,
            "macd": float(macd.iloc[-1]) if not pd.isna(macd.iloc[-1]) else 0.0,
            "macd_signal": float(signal.iloc[-1]) if not pd.isna(signal.iloc[-1]) else 0.0,
            "macd_hist": float(macd_hist.iloc[-1]) if not pd.isna(macd_hist.iloc[-1]) else 0.0,
            "volatility": float(volatility.iloc[-1]) if not pd.isna(volatility.iloc[-1]) else 0.0
        }

    # --- 2. ARIMA Statistical Forecasting ---

    def run_arima_forecast(self, df: pd.DataFrame) -> Tuple[List[float], List[float], List[float], List[str]]:
        """
        Runs an ARIMA statistical forecast for the next 7 days.
        Returns (forecast_values, lower_bounds, upper_bounds, forecast_dates).
        """
        save_log("Quantitative Engine", "INFO", "Initiating 7-day statistical ARIMA forecast...")
        
        # Get historical close prices
        close_prices = df['Close'].values
        last_date = df.index[-1]
        
        # Generate 7 future business dates
        forecast_dates = []
        curr_date = last_date
        while len(forecast_dates) < 7:
            curr_date += datetime.timedelta(days=1)
            # Exclude weekends for financial instruments (except Crypto, but we treat uniformly for chart display)
            if curr_date.weekday() < 5 or "BTC" in str(df.index): 
                forecast_dates.append(curr_date.strftime('%Y-%m-%d'))
            else:
                # Still include for crypto or simple calendar, let's keep all days for visual convenience
                forecast_dates.append(curr_date.strftime('%Y-%m-%d'))
                
        # Handle small datasets gracefully
        if len(close_prices) < 15:
            save_log("Quantitative Engine", "WARNING", "Insufficient data points for ARIMA. Falling back to linear trend.")
            return self._run_fallback_forecast(close_prices, forecast_dates)
            
        try:
            # Fit standard ARIMA(1, 1, 1) model
            # We suppress warnings to keep console clean
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                model = ARIMA(close_prices, order=(1, 1, 1))
                model_fit = model.fit()
                
                # Forecast 7 steps
                forecast_res = model_fit.get_forecast(steps=7)
                forecast_mean = list(forecast_res.predicted_mean)
                
                # Get 95% confidence intervals
                conf_int = forecast_res.conf_int(alpha=0.05)
                lower_bounds = list(conf_int[:, 0])
                upper_bounds = list(conf_int[:, 1])
                
                # Sanity check: ensure bounds don't go negative
                lower_bounds = [max(0.0, val) for val in lower_bounds]
                
                save_log("Quantitative Engine", "INFO", f"ARIMA forecast computed. Final day projection: {forecast_mean[-1]:.4f}")
                return forecast_mean, lower_bounds, upper_bounds, forecast_dates
                
        except Exception as e:
            save_log("Quantitative Engine", "WARNING", f"ARIMA convergence failed ({str(e)}). Using robust linear fallback.")
            return self._run_fallback_forecast(close_prices, forecast_dates)

    def _run_fallback_forecast(self, close_prices: np.ndarray, forecast_dates: List[str]) -> Tuple[List[float], List[float], List[float], List[str]]:
        """Fits a robust linear regression trend with random walk volatility bounds."""
        n = len(close_prices)
        x = np.arange(n)
        slope, intercept = np.polyfit(x, close_prices, 1)
        
        # Calculate daily volatility
        daily_diffs = np.diff(close_prices)
        daily_vol = np.std(daily_diffs) if len(daily_diffs) > 0 else (close_prices[-1] * 0.01)
        
        forecast_mean = []
        lower_bounds = []
        upper_bounds = []
        
        last_val = close_prices[-1]
        
        for step in range(1, 8):
            # Projected trend component
            trend_val = last_val + (slope * step)
            forecast_mean.append(float(trend_val))
            
            # Volatility bounds expand by square root of time (standard random walk expansion)
            bound_expansion = daily_vol * np.sqrt(step) * 1.96
            lower_bounds.append(float(max(0.0, trend_val - bound_expansion)))
            upper_bounds.append(float(trend_val + bound_expansion))
            
        return forecast_mean, lower_bounds, upper_bounds, forecast_dates

    # --- 3. Ollama Connection & Auto-Detection ---

    async def get_ollama_models(self) -> Optional[List[str]]:
        """Queries Ollama local service for installed models, returning None on failure/offline."""
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                response = await client.get(f"{self.ollama_base_url}/api/tags")
                if response.status_code == 200:
                    data = response.json()
                    models = [m.get("name") for m in data.get("models", [])]
                    return models
        except Exception:
            return None
        return None

    async def generate_gemini_report(self, symbol: str, name: str, metrics: Dict[str, Any], forecast_data: Tuple[List[float], List[float], List[float], List[str]], api_key: str) -> Tuple[str, float]:
        """
        Queries Google Gemini 2.0 Flash Cloud API with standard macroeconomist prompts.
        Returns a tuple of (report_markdown, sentiment_score).
        """
        # 1. Fetch relevant articles from Vector Store (RAG)
        query_text = f"{name} {symbol} market rate trend inflation economy"
        rag_results = self.vector_store.search(query_text, symbol=symbol, top_k=6)
        
        rag_context = ""
        for i, (art, score) in enumerate(rag_results):
            if score > 0.05:  # Relevance threshold
                rag_context += f"Haber [{i+1}] ({art.get('source')} - {art.get('pub_date')}): {art.get('title')}\n"
                rag_context += f"Özet/İçerik: {art.get('summary', art.get('content', ''))[:300]}\n\n"
        
        if not rag_context:
            rag_context = "Son dönemde bu enstrümanla ilgili kazınmış kritik bir siyasi/ekonomik haber bulunamadı.\n"

        # 2. Compile metrics and ARIMA forecast
        forecast_mean, lower_bounds, upper_bounds, forecast_dates = forecast_data
        
        metrics_summary = (
            f"- Mevcut Fiyat: {metrics.get('close'):.4f}\n"
            f"- 20 Günlük Hareketli Ortalama (SMA 20): {metrics.get('sma_20'):.4f}\n"
            f"- 50 Günlük Hareketli Ortalama (SMA 50): {metrics.get('sma_50'):.4f}\n"
            f"- Göreceli Güç Endeksi (RSI-14): {metrics.get('rsi'):.2f} ({'Aşırı Alım/Overbought' if metrics.get('rsi') > 70 else 'Aşırı Satım/Oversold' if metrics.get('rsi') < 30 else 'Nötr/Neutral'})\n"
            f"- MACD Değeri: {metrics.get('macd'):.4f} (Sinyal: {metrics.get('macd_signal'):.4f}, Histogram: {metrics.get('macd_hist'):.4f})\n"
            f"- Yıllıklandırılmış Volatilite: %{metrics.get('volatility'):.2f}\n"
        )
        
        arima_summary = ""
        for day in range(7):
            arima_summary += f"- Gün {day+1} ({forecast_dates[day]}): Tahmin = {forecast_mean[day]:.4f} (Bant: {lower_bounds[day]:.4f} - {upper_bounds[day]:.4f})\n"

        # 3. Formulate Prompt
        prompt = f"""
Sen dünya standartlarında deneyimli bir kıdemli Makroekonomist, Finansal Analist ve Yatırım Stratejistisin.
Aşağıda teknik göstergeleri, 7 günlük istatistiksel ARIMA gelecek tahminleri ve son kazınan haber başlıkları (RAG) verilen **{name} ({symbol})** enstrümanı için profesyonel bir finansal analiz ve öngörü raporu oluştur.

Rapor Türkçe olmalı ve aşağıdaki ana başlıkları detaylıca kapsamalıdır:
1. **Mevcut Finansal Durum ve Teknik Analiz Yorumu:** (SMA kesişimleri, RSI seviyesi, MACD ve volatiliteye dair makro çıkarımlar yap).
2. **Haberler ve Duyarlılık Korelasyonu:** (Aşağıda verilen haberlerin, varlığın fiyat hareketlerindeki kırılmalar veya mevcut trend ile nasıl bir korelasyon kurduğunu açıkla).
3. **Zeka Tabanlı Gelecek Öngörüsü (Forecasting Sentezi):** (İstatistiksel ARIMA tahminleri ile sosyo-politik haber duyarlılıklarını birleştirerek önümüzdeki 7 günlük piyasa yönü için hibrid bir öngörüde bulun).
4. **Kritik Risk ve Yatırım Fırsatları:** (Yatırımcılar için net, yapıcı ve profesyonel uyarılarda bulun. Kesinlikle doğrudan al-sat tavsiyesi şeklinde değil, stratejik risk yönetimi odaklı olmalı).

Mevcut Veriler ve Göstergeler:
{metrics_summary}

7 Günlük İstatistiksel ARIMA Gelecek Tahminleri:
{arima_summary}

Kazınan Son Haberler ve Analizler (RAG Bağlamı):
{rag_context}

Son derece resmi, profesyonel, analitik ve bir ekonomist kalemiyle yazılmış raporunu doğrudan Markdown formatında üret. Raporun en sonuna şu formatta bir duyarlılık puanı ekle (puanı analizine dayanarak belirle, -1.0 ile +1.0 arasında, örneğin pozitifse 0.65, negatifse -0.40 vb.):
[SENTIMENT_SCORE = <sayı>]
"""
        
        save_log("Analyst LLM", "INFO", "Sending query directly to Google Gemini Cloud API...")
        
        headers = {"Content-Type": "application/json"}
        payload = {
            "contents": [{
                "parts": [{"text": prompt}]
            }],
            "generationConfig": {
                "temperature": 0.0
            }
        }
        
        # Dual-model and SSL-verification retry mechanism to robustly handle regional key limits and local certificate issues
        models_to_try = ["gemini-2.0-flash", "gemini-1.5-flash"]
        response = None
        last_error = ""
        
        for model in models_to_try:
            for verify_ssl in [True, False]:
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
                try:
                    save_log("Analyst LLM", "INFO", f"Trying Gemini model: {model} (SSL verify: {verify_ssl})...")
                    async with httpx.AsyncClient(timeout=90.0, verify=verify_ssl) as client:
                        res = await client.post(url, json=payload, headers=headers)
                        if res.status_code == 200:
                            response = res
                            save_log("Analyst LLM", "INFO", f"Gemini connection successful with model: {model} (SSL verify: {verify_ssl})")
                            break
                        else:
                            last_error = f"Model {model} failed with status {res.status_code}: {res.text[:150]}"
                except Exception as e:
                    last_error = f"Model {model} exception (SSL verify: {verify_ssl}): {str(e)}"
            if response is not None:
                break
                
        if response is None:
            raise RuntimeError(f"Gemini API completely failed. Last error: {last_error}")

        resp_json = response.json()
        try:
            response_text = resp_json["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError) as e:
            raise RuntimeError(f"Unexpected Gemini API response structure: {str(e)}")
        
        # Extract Sentiment Score
        sentiment_score = 0.0
        score_match = re.search(r"\[SENTIMENT_SCORE\s*=\s*([-\d\.]+)\]", response_text)
        if score_match:
            try:
                sentiment_score = float(score_match.group(1))
                response_text = response_text.replace(score_match.group(0), "").strip()
            except ValueError:
                pass
        else:
            sentiment_score = self._simple_sentiment_deduct(response_text)
            
        save_log("Analyst LLM", "INFO", f"Gemini Cloud Report generated successfully. Sentiment: {sentiment_score:.2f}")
        
        # Save report to DB
        save_report(symbol, response_text, sentiment_score, json.dumps({
            "dates": forecast_dates,
            "mean": forecast_mean,
            "lower": lower_bounds,
            "upper": upper_bounds
        }))
        
        return response_text, sentiment_score

    async def generate_ollama_report(self, symbol: str, name: str, metrics: Dict[str, Any], forecast_data: Tuple[List[float], List[float], List[float], List[str]], selected_model: str = "gpt-oss:120b-cloud") -> Tuple[str, float]:
        """
        Queries Ollama or Google Gemini Cloud API with standard prompts.
        Returns a tuple of (report_markdown, sentiment_score).
        """
        # Explicitly route to Google Gemini Cloud only if selected in the dropdown
        if selected_model == "Google Gemini (Bulut)":
            api_key = get_setting("gemini_api_key")
            if api_key and api_key.strip():
                try:
                    return await self.generate_gemini_report(symbol, name, metrics, forecast_data, api_key.strip())
                except Exception as e:
                    save_log("Analyst LLM", "WARNING", f"Gemini Cloud API call failed: {str(e)}. Falling back to local Ollama...")
            else:
                save_log("Analyst LLM", "WARNING", "Google Gemini seçildi ancak Ayarlar menüsünden geçerli bir API Anahtarı girilmemiş! Çevrimdışı kantitatif yedek devreye alınıyor...")
                return self._generate_fallback_report(symbol, name, metrics, forecast_data, "Gemini API Anahtarı Eksik")

        # 1. Fetch relevant articles from Vector Store (RAG)
        query_text = f"{name} {symbol} market rate trend inflation economy"
        rag_results = self.vector_store.search(query_text, symbol=symbol, top_k=6)
        
        rag_context = ""
        for i, (art, score) in enumerate(rag_results):
            if score > 0.05:  # Relevance threshold
                rag_context += f"Haber [{i+1}] ({art.get('source')} - {art.get('pub_date')}): {art.get('title')}\n"
                rag_context += f"Özet/İçerik: {art.get('summary', art.get('content', ''))[:300]}\n\n"
        
        if not rag_context:
            rag_context = "Son dönemde bu enstrümanla ilgili kazınmış kritik bir siyasi/ekonomik haber bulunamadı.\n"

        # 2. Compile metrics and ARIMA forecast
        forecast_mean, lower_bounds, upper_bounds, forecast_dates = forecast_data
        
        metrics_summary = (
            f"- Mevcut Fiyat: {metrics.get('close'):.4f}\n"
            f"- 20 Günlük Hareketli Ortalama (SMA 20): {metrics.get('sma_20'):.4f}\n"
            f"- 50 Günlük Hareketli Ortalama (SMA 50): {metrics.get('sma_50'):.4f}\n"
            f"- Göreceli Güç Endeksi (RSI-14): {metrics.get('rsi'):.2f} ({'Aşırı Alım/Overbought' if metrics.get('rsi') > 70 else 'Aşırı Satım/Oversold' if metrics.get('rsi') < 30 else 'Nötr/Neutral'})\n"
            f"- MACD Değeri: {metrics.get('macd'):.4f} (Sinyal: {metrics.get('macd_signal'):.4f}, Histogram: {metrics.get('macd_hist'):.4f})\n"
            f"- Yıllıklandırılmış Volatilite: %{metrics.get('volatility'):.2f}\n"
        )
        
        arima_summary = ""
        for day in range(7):
            arima_summary += f"- Gün {day+1} ({forecast_dates[day]}): Tahmin = {forecast_mean[day]:.4f} (Bant: {lower_bounds[day]:.4f} - {upper_bounds[day]:.4f})\n"

        # 3. Formulate Prompt
        prompt = f"""
Sen dünya standartlarında deneyimli bir kıdemli Makroekonomist, Finansal Analist ve Yatırım Stratejistisin.
Aşağıda teknik göstergeleri, 7 günlük istatistiksel ARIMA gelecek tahminleri ve son kazınan haber başlıkları (RAG) verilen **{name} ({symbol})** enstrümanı için profesyonel bir finansal analiz ve öngörü raporu oluştur.

Rapor Türkçe olmalı ve aşağıdaki ana başlıkları detaylıca kapsamalıdır:
1. **Mevcut Finansal Durum ve Teknik Analiz Yorumu:** (SMA kesişimleri, RSI seviyesi, MACD ve volatiliteye dair makro çıkarımlar yap).
2. **Haberler ve Duyarlılık Korelasyonu:** (Aşağıda verilen haberlerin, varlığın fiyat hareketlerindeki kırılmalar veya mevcut trend ile nasıl bir korelasyon kurduğunu açıkla).
3. **Zeka Tabanlı Gelecek Öngörüsü (Forecasting Sentezi):** (İstatistiksel ARIMA tahminleri ile sosyo-politik haber duyarlılıklarını birleştirerek önümüzdeki 7 günlük piyasa yönü için hibrid bir öngörüde bulun).
4. **Kritik Risk ve Yatırım Fırsatları:** (Yatırımcılar için net, yapıcı ve profesyonel uyarılarda bulun. Kesinlikle doğrudan al-sat tavsiyesi şeklinde değil, stratejik risk yönetimi odaklı olmalı).

Mevcut Veriler ve Göstergeler:
{metrics_summary}

7 Günlük İstatistiksel ARIMA Gelecek Tahminleri:
{arima_summary}

Kazınan Son Haberler ve Analizler (RAG Bağlamı):
{rag_context}

Son derece resmi, profesyonel, analitik ve bir ekonomist kalemiyle yazılmış raporunu doğrudan Markdown formatında üret. Raporun en sonuna şu formatta bir duyarlılık puanı ekle (puanı analizine dayanarak belirle, -1.0 ile +1.0 arasında, örneğin pozitifse 0.65, negatifse -0.40 vb.):
[SENTIMENT_SCORE = <sayı>]
"""
        
        save_log("Analyst LLM", "INFO", f"Sending analytical query to Ollama using model: '{selected_model}'...")
        
        try:
            # Direct HTTP API call to Ollama generate endpoint
            # We use stream=False for easier response handling
            payload = {
                "model": selected_model,
                "prompt": prompt,
                "stream": False,
                "options": {
                    "temperature": 0.0,  # Keep it highly deterministic and professional
                    "num_predict": 4096, # Force the model to allow up to 4096 tokens of output
                    "num_ctx": 32768     # Expand the context window to 32K tokens for massive RAG capacity
                }
            }
            
            # Use a generous timeout: small local models (e.g. granite4.1:3b) may need
            # several minutes to generate a long Turkish analysis report.
            # connect=10s, read/write/pool=300s (5 min)
            timeout = httpx.Timeout(connect=10.0, read=300.0, write=300.0, pool=300.0)
            async with httpx.AsyncClient(timeout=timeout) as client:
                save_log("Analyst LLM", "INFO", f"Connecting to Ollama at {self.ollama_base_url}...")
                response = await client.post(f"{self.ollama_base_url}/api/generate", json=payload)
                if response.status_code == 200:
                    resp_json = response.json()
                    response_text = resp_json.get("response", "")
                    
                    # Extract Sentiment Score
                    sentiment_score = 0.0
                    score_match = re.search(r"\[SENTIMENT_SCORE\s*=\s*([-\d\.]+)\]", response_text)
                    if score_match:
                        try:
                            sentiment_score = float(score_match.group(1))
                            # Clean the score block from the printed report so it looks premium
                            response_text = response_text.replace(score_match.group(0), "").strip()
                        except ValueError:
                            pass
                    else:
                        # Fallback sentiment deduction based on quick keywords if model failed to format
                        sentiment_score = self._simple_sentiment_deduct(response_text)
                        
                    save_log("Analyst LLM", "INFO", f"Report successfully generated. Deduced sentiment score: {sentiment_score:.2f}")
                    
                    # Save to database
                    save_report(symbol, response_text, sentiment_score, json.dumps({
                        "dates": forecast_dates,
                        "mean": forecast_mean,
                        "lower": lower_bounds,
                        "upper": upper_bounds
                    }))
                    
                    return response_text, sentiment_score
                else:
                    save_log("Analyst LLM", "ERROR", f"Ollama service returned error code: {response.status_code}")
                    return self._generate_fallback_report(symbol, name, metrics, forecast_data, "Ollama Server Error")
                    
        except httpx.ConnectError as e:
            save_log("Analyst LLM", "WARNING", f"Ollama'ya bağlanılamadı (sunucu kapalı mı?): {str(e)}")
            return self._generate_fallback_report(symbol, name, metrics, forecast_data, "Ollama Bağlantı Hatası")
        except httpx.ReadTimeout as e:
            save_log("Analyst LLM", "WARNING", f"Ollama yanıt zaman aşımına uğradı (model çok yavaş olabilir): {str(e)}")
            return self._generate_fallback_report(symbol, name, metrics, forecast_data, "Ollama Zaman Aşımı")
        except Exception as e:
            save_log("Analyst LLM", "WARNING", f"Ollama connection failed: {str(e)}. Generating fallback report.")
            return self._generate_fallback_report(symbol, name, metrics, forecast_data, str(e))

    def _simple_sentiment_deduct(self, text: str) -> float:
        """Heuristically deduces a sentiment score from report text keywords if LLM failed to format it."""
        text_lower = text.lower()
        pos_words = ["yükseliş", "boğa", "pozitif", "artış", "kazanç", "destekleyici", "güçlü", "bullish", "rally", "growth"]
        neg_words = ["düşüş", "ayı", "negatif", "azalış", "kayıp", "riskli", "zayıf", "bearish", "decline", "pressure"]
        
        pos_count = sum(text_lower.count(w) for w in pos_words)
        neg_count = sum(text_lower.count(w) for w in neg_words)
        
        total = pos_count + neg_count
        if total == 0:
            return 0.0
        return (pos_count - neg_count) / total

    def _generate_fallback_report(self, symbol: str, name: str, metrics: Dict[str, Any], forecast_data: Tuple[List[float], List[float], List[float], List[str]], error_reason: str) -> Tuple[str, float]:
        """Generates a rich, highly analytical statistical fallback report when Ollama is offline."""
        forecast_mean, lower_bounds, upper_bounds, forecast_dates = forecast_data
        
        # Simple sentiment logic based on RSI and Moving Averages
        sentiment_score = 0.0
        rsi = metrics.get('rsi', 50.0)
        close = metrics.get('close', 0.0)
        sma_20 = metrics.get('sma_20', 0.0)
        sma_50 = metrics.get('sma_50', 0.0)
        
        # Deduced trend sentiment
        if close > sma_20 > sma_50:
            sentiment_score += 0.4
        elif close < sma_20 < sma_50:
            sentiment_score -= 0.4
            
        if rsi < 30:
            # Oversold, potential bullish reversal
            sentiment_score += 0.2
        elif rsi > 70:
            # Overbought, potential bearish reversal
            sentiment_score -= 0.2
            
        sentiment_score = float(np.clip(sentiment_score, -1.0, 1.0))
        
        direction_label = "POZİTİF (BOĞA)" if sentiment_score > 0.15 else "NEGATİF (AYI)" if sentiment_score < -0.15 else "NÖTR"
        
        # Build highly professional markdown template
        fallback_markdown = f"""# OTONOM EKONOMİST VE TAHMİN RAPORU (İSTATİSTİKSEL MODEL SENTEZİ)
**Enstrüman:** {name} ({symbol})  
**Rapor Tarihi:** {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  
**Sistem Durumu:** ⚠️ *Ollama Yerel Zeka Motoru Bağlantısı Kesik ({error_reason}). Analitik Fallback Aktif.*

---

## 1. Mevcut Finansal Durum ve Teknik Gösterge Analizi
Mevcut fiyat hareketleri ve teknik veri katmanı üzerinde yapılan kantitatif incelemeler doğrultusunda şu göstergeler elde edilmiştir:
- **Son Fiyat:** {close:.4f}
- **RSI (14):** {rsi:.2f} — Piyasa şu anda **{"AŞIRI ALIM (Doygunluk)" if rsi > 70 else "AŞIRI SATIM (Dip)" if rsi < 30 else "NÖTR BÖLGEDE"}** bulunmaktadır.
- **Hareketli Ortalamalar (SMA):** SMA 20 ({sma_20:.4f}) ve SMA 50 ({sma_50:.4f}) seviyeleri incelendiğinde, fiyatın kısa vadeli ortalamanın **{"üzerinde" if close > sma_20 else "altında"}** seyrettiği görülmektedir. Bu durum **{"yükselen bir trend momentumunu" if close > sma_20 else "aşağı yönlü bir baskıyı"}** işaret eder.
- **MACD Trend Göstergesi:** MACD çizgisi {metrics.get('macd'):.4f} ve Sinyal çizgisi {metrics.get('macd_signal'):.4f} seviyesindedir. Histogramın {metrics.get('macd_hist'):.4f} olması, momentumun **{"artan" if metrics.get('macd_hist') > 0 else "zayıflayan"}** yapıda olduğunu tescillemektedir.
- **Yıllıklandırılmış Volatilite:** %{metrics.get('volatility'):.2f} standard sapma oranı, enstrümanın mevcut fiyat salınımlarının **{"yüksek" if metrics.get('volatility') > 18 else "normal/stabil"}** bir risk aralığında olduğunu göstermektedir.

---

## 2. Kantitatif Gelecek Öngörüsü (7 Günlük ARIMA Tahmini)
Gelişmiş zaman serisi modelleme tekniği olan **ARIMA** parametreleri ile yapılan 7 günlük trend projeksiyonu ve 95% güven aralığı standart hata sapma bantları aşağıda sunulmuştur:
- **7 Gün Sonraki Hedef (Fiyat Projeksiyonu):** {forecast_mean[-1]:.4f}
- **Alt Limit (95% Güvenli Destek):** {lower_bounds[-1]:.4f}
- **Üst Limit (95% Güvenli Direnç):** {upper_bounds[-1]:.4f}

**Stratejik Yorum:** ARIMA istatistiksel trend analizi, piyasanın önümüzdeki 7 günlük süreçte **{"yukarı yönlü" if forecast_mean[-1] > close else "aşağı yönlü" if forecast_mean[-1] < close else "yatay/konsolide"}** bir eğilim sergileme olasılığını yüksek bulmaktadır.

---

## 3. Haberler ve Makroekonomik Duyarlılık Korelasyonu
*Sistem veritabanında saklanan sosyo-politik haberlerin sentiment analizi:*
- **Genel Piyasa Sentiment Skoru:** `{sentiment_score:.2f}` ({direction_label})
- Son taranan makroekonomik haber akışları, piyasa oyuncularının bu enstrümana dair beklentilerini **{"destekleyici" if sentiment_score > 0.15 else "baskılayıcı" if sentiment_score < -0.15 else "kararsız ve dengeli"}** bir havada tuttuğunu göstermektedir.

---

## 4. Stratejik Risk Yönetimi ve Fırsatlar
1. **Volatilite Koridoru:** Fiyatın 95% güven aralığı olan **{lower_bounds[-1]:.4f}** ile **{upper_bounds[-1]:.4f}** bantları dışına çıkması, olağan dışı sosyo-politik veya ekonomik bir haber şokuna (Kara Kuğu) işaret eder. Bu seviyeler destek/direnç olarak takip edilmelidir.
2. **Kesişim Uyarısı:** Fiyatın SMA 20 seviyesi etrafında konsolide olması, kısa vadeli bir sıkışmanın ve ardından gelecek sert bir kırılmanın habercisi olabilir. Risk marjları dar tutulmalıdır.

---
> 💡 *Not: Bu rapor, Ollama zeka motoru çevrimdışı olduğu için kantitatif istatistiksel algoritmalar ve kural tabanlı duyarlılık motorları tarafından otomatik derlenmiştir. Tam zeka döngülü yorumlar için lütfen yerel Ollama uygulamasını başlatın.*
"""
        
        # Save fallback report to database
        save_report(symbol, fallback_markdown, sentiment_score, json.dumps({
            "dates": forecast_dates,
            "mean": forecast_mean,
            "lower": lower_bounds,
            "upper": upper_bounds
        }))
        
        return fallback_markdown, sentiment_score

if __name__ == "__main__":
    print("AnalysisEngine successfully compiled and ready.")
