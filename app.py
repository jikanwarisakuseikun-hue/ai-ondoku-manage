import streamlit as st
import azure.cognitiveservices.speech as speechsdk
import os
import io
from datetime import datetime, timedelta
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload
import pandas as pd

st.set_page_config(page_title="AI音読アドバイザー Max Pro", layout="centered")
st.title("🗣️ AI音読システム（成績表＆音声自動提出）")
st.write("画面に表示されている英文を読んで、録音して提出しよう！")

# --- 🌟 先生の設定（session_state）の初期化 ---
if "teacher_text" not in st.session_state:
    st.session_state.teacher_text = "Welcome to our school. Let's practice English together!"
if "teacher_unit" not in st.session_state:
    st.session_state.teacher_unit = "Unit 1 Part 1"

# --- 1. アカウント＆負荷分散スイッチ ---
attendance_type = st.radio(
    "あなたの 出席番号（または班） を選んでください：",
    ["奇数番号 (1, 3, 5...)", "偶数番号 (2, 4, 6...)"],
    horizontal=True
)

if "奇数" in attendance_type:
    azure_key = st.secrets["KEY_KISU"]
else:
    azure_key = st.secrets["KEY_GUSU"]

azure_region = st.secrets["AZURE_REGION"]

# --- 2. 生徒の個人情報入力 ---
col1, col2, col3 = st.columns(3)
with col1:
    class_name = st.text_input("クラス：", placeholder="例: 1組")
with col2:
    student_num = st.text_input("出席番号：", placeholder="例: 05")
with col3:
    student_name = st.text_input("氏名：", placeholder="例: 田中太郎")

# --- 🌟 先生が設定したお題の表示エリア ---
st.markdown("---")
st.markdown(f"### 📖 今日の課題: **{st.session_state.teacher_unit}**")
st.info(f"👇 この英文を大きな声で読んでね👇\n\n### **{st.session_state.teacher_text}**")
st.markdown("---")

# 内部処理用にお題を代入
unit_name = st.session_state.teacher_unit
reference_text = st.session_state.teacher_text

st.subheader("🎤 録音スタート")
audio_value = st.audio_input("マイクボタンを押して英語を読んでね")

# 点数や判定結果をガッチリ記憶しておくための「金庫」
if "saved_results" not in st.session_state:
    st.session_state.saved_results = None

if audio_value:
    if st.session_state.saved_results is None:
        st.info("AIが発音を多角的に分析中... 🤖")
        
        audio_bytes = audio_value.read()
        with open("temp_audio.wav", "wb") as f:
            f.write(audio_bytes)
            
        try:
            speech_config = speechsdk.SpeechConfig(subscription=azure_key, region=azure_region)
            audio_config = speechsdk.audio.AudioConfig(filename="temp_audio.wav")
            
            pronunciation_config = speechsdk.PronunciationAssessmentConfig(
                json_string=f'{{"referenceText":"{reference_text}","gradingSystem":"HundredMark","granularity":"Word","phonemeAlphabet":"IPA"}}'
            )
            pronunciation_config.enable_prosody_assessment()
            
            speech_recognizer = speechsdk.SpeechRecognizer(speech_config=speech_config, audio_config=audio_config)
            pronunciation_config.apply_to(speech_recognizer)
            result = speech_recognizer.recognize_once_async().get()
            
            if result.reason == speechsdk.ResultReason.RecognizedSpeech:
                pron_result = speechsdk.PronunciationAssessmentResult(result)
                
                score_acc = int(pron_result.accuracy_score)
                score_flu = int(pron_result.fluency_score)
                score_comp = int(pron_result.completeness_score)
                score_pros = int(pron_result.prosody_score) if hasattr(pron_result, 'prosody_score') else 85
                
                final_score = int((score_acc + score_flu + score_pros + score_comp) / 4)
                
                words_data = []
                for word in pron_result.words:
                    words_data.append({"word": word.word, "error_type": word.error_type})
                
                st.session_state.saved_results = {
                    "final_score": final_score,
                    "score_acc": score_acc,
                    "score_flu": score_flu,
                    "score_pros": score_pros,
                    "score_comp": score_comp,
                    "words_data": words_data,
                    "audio_bytes": audio_bytes
                }
            else:
                st.error("AIがうまく声を聴き取れませんでした。もう一度試してね。")
        except Exception as e:
            st.error(f"❌ エラーが発生しました: {e}")
        finally:
            if os.path.exists("temp_audio.wav"):
                os.remove("temp_audio.wav")

    if st.session_state.saved_results:
        res = st.session_state.saved_results
        
        st.success(f"🎉 総合スコア: {res['final_score']} 点 / 100点")
        
        st.markdown("### 📈 あなたの発音ステータス（観点別）")
        chart_data = pd.DataFrame({
            "観点": ["正確さ(音)", "流暢さ(スピード)", "抑揚(リズム)", "完成度(読み飛ばし)"],
            "スコア": [res['score_acc'], res['score_flu'], res['score_pros'], res['score_comp']]
        })
        st.bar_chart(chart_data.set_index("観点"))
        
        colored_words = []
        for word in res['words_data']:
            if word['error_type'] == "None":
                colored_words.append(f":green[{word['word']}]")
            elif word['error_type'] == "Mispronunciation":
                colored_words.append(f":red[{word['word']}]")
            elif word['error_type'] == "Omission":
                colored_words.append(f"~~{word['word']}~~")
        st.subheader(" ".join(colored_words))
        
        st.markdown("---")
        st.subheader("📮 先生への自動提出")
        
        if not (class_name and student_num and student_name):
            st.warning("⚠️ 提出するには、クラス・出席番号・氏名をすべて入力してください。")
        else:
            if st.button("📤 この結果と音声を先生に提出する", type="primary"):
                with st.spinner("先生のGoogle Driveへ送信中... 🚀"):
                    try:
                        robot_email = st.secrets["ROBOT_EMAIL"]
                        client_id = st.secrets["ROBOT_CLIENT_ID"]
                        formatted_private_key = st.secrets["ROBOT_PRIVATE_KEY"]
                        
                        info = {
                            "type": "service_account",
                            "project_id": "ai-ondoku-final-go",
                            "private_key_id": "google_cloud_key",
                            "private_key": formatted_private_key,
                            "client_email": robot_email,
                            "client_id": client_id,
                            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                            "token_uri": "https://oauth2.googleapis.com/token",
                            "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs"
                        }
                        
                        creds = service_account.Credentials.from_service_account_info(
                            info, 
                            scopes=[
                                "https://www.googleapis.com/auth/drive.file",
                                "https://www.googleapis.com/auth/spreadsheets"
                            ]
                        )
                        drive_service = build('drive', 'v3', credentials=creds)
                        sheets_service = build('sheets', 'v4', credentials=creds)
                        
                        folder_id = st.secrets["GOOGLE_DRIVE_FOLDER_ID"]
                        spreadsheet_id = st.secrets["GOOGLE_SHEET_ID"]
                        
                        filename = f"{class_name}_{student_num}番_{student_name}_{unit_name}_{res['final_score']}点.wav"
                        file_metadata = {
                            'name': filename,
                            'parents': [folder_id],
                            'description': f"総合点:{res['final_score']}, 正確さ:{res['score_acc']}, 流暢さ:{res['score_flu']}, 抑揚:{res['score_pros']}, 完成度:{res['score_comp']}"
                        }
                        media = MediaIoBaseUpload(io.BytesIO(res['audio_bytes']), mimetype='audio/wav')
                        
                        uploaded_file = drive_service.files().create(
                            body=file_metadata, media_body=media, fields='id', supportsAllDrives=True
                        ).execute()
                        file_id = uploaded_file.get('id')
                        
                        audio_link = f"https://drive.google.com/file/d/{file_id}/view?usp=drivesdk"
                        
                        now_jst = datetime.utcnow() + timedelta(hours=9)
                        timestamp = now_jst.strftime('%Y-%m-%d %H:%M:%S')
                        
                        row_data = [
                            timestamp, class_name, student_num, student_name, unit_name,
                            res['final_score'], res['score_acc'], res['score_flu'], res['score_pros'], res['score_comp'], audio_link
                        ]
                        
                        body = {'values': [row_data]}
                        sheets_service.spreadsheets().values().append(
                            spreadsheetId=spreadsheet_id,
                            range="シート1!A:K",
                            valueInputOption="USER_ENTERED",
                            insertDataOption="INSERT_ROWS",
                            body=body
                        ).execute()
                        
                        st.balloons()
                        st.success("🎉 提出が完了しました！")
                        st.session_state.saved_results = None
                        
                    except Exception as google_error:
                        st.error(f"❌ Googleシステムへの送信に失敗しました: {google_error}")
else:
    st.session_state.saved_results = None

# --- 🌟 3. 🛠️ 先生用・管理者メニュー（画面最下部に隠し配置） ---
st.markdown(" ")
st.markdown(" ")
with st.expander("🛠️ 先生用・管理者メニュー（ここから課題を変更できます）"):
    password = st.text_input("パスワードを入力してください：", type="password")
    if password == "sensei777": # 👈 パスワードを変更したい場合はここを書き換えてください
        st.success("認証成功！今日の課題を設定してください。")
        new_unit = st.text_input("新しい単元名：", value=st.session_state.teacher_unit)
        new_text = st.text_area("新しい英文（生徒の画面に大きく表示されます）：", value=st.session_state.teacher_text)
        
        if st.button("🔄 課題を更新する"):
            st.session_state.teacher_unit = new_unit
            st.session_state.teacher_text = new_text
            st.success("課題を更新しました！生徒画面に反映されています。")
            st.rerun()
