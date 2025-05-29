import os
import logging
from io import BytesIO
from pathlib import Path
import re # For sanitizing voice names

from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes,
)
from telegram.constants import ChatAction

# --- Configuration & Constants ---
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN") # Load from environment variable
if not BOT_TOKEN:
    raise ValueError("TELEGRAM_BOT_TOKEN environment variable not set!")

BASE_DIR = Path(__file__).resolve().parent
VOICE_DIR = BASE_DIR / "voices"
DEFAULT_VOICE_NAME = "default" # A conceptual default, or ensure 'default.wav' exists

# Callback data prefixes
CALLBACK_PREFIX_VOICE = "select_voice:"

# --- State (in-memory, could be moved to context.bot_data or a database for persistence) ---
user_selected_voice = {}  # {user_id: voice_name}
awaiting_voice_upload = {} # {user_id: voice_name_to_save}

# --- Logging Setup ---
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- Helper Functions ---

# Dummy TTS using voice name and text
async def tts(voice_name: str, text: str) -> BytesIO:
    """
    Generates Text-to-Speech audio.
    Replace with your actual TTS implementation.
    This dummy version doesn't use the voice_name file.
    """
    logger.info(f"TTS called for voice '{voice_name}' with text: '{text[:30]}...'")
    # In a real TTS, you'd load/use the actual voice file from VOICE_DIR / f"{voice_name}.wav"
    return BytesIO(f"FAKE_AUDIO_FOR_{voice_name}".encode()) # Simple fake audio

def get_available_voices() -> list[str]:
    """Returns a list of available voice names (without .wav extension)."""
    if not VOICE_DIR.exists():
        return []
    try:
        # Only list files, ignore directories, case-insensitive .wav check
        return [
            f.stem
            for f in VOICE_DIR.iterdir()
            if f.is_file() and f.suffix.lower() == ".wav"
        ]
    except OSError as e:
        logger.error(f"Error listing voices in {VOICE_DIR}: {e}")
        return []

def sanitize_voice_name(name: str) -> str:
    """Sanitizes a voice name to be filesystem-friendly."""
    # Remove problematic characters, keep alphanumeric, underscore, hyphen
    name = re.sub(r'[^\w\-]', '', name)
    return name[:50] # Limit length

# --- Command Handlers ---

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Welcome! I can convert your text to speech.\n"
        "Use /voice to select a voice.\n"
        "Use /newvoice <name> to add a new voice (then send a voice message or .wav file)."
    )

async def voice_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    voices = get_available_voices()
    if not voices:
        await update.message.reply_text(
            "No voices available. Use /newvoice <name> to add one."
        )
        return

    keyboard = [
        [InlineKeyboardButton(v, callback_data=f"{CALLBACK_PREFIX_VOICE}{v}")]
        for v in voices
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Select a voice:", reply_markup=reply_markup)

async def newvoice_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    if not context.args:
        await update.message.reply_text("Usage: /newvoice <voice_name>")
        return

    raw_voice_name = " ".join(context.args)
    voice_name = sanitize_voice_name(raw_voice_name)

    if not voice_name:
        await update.message.reply_text("Invalid voice name. Please use alphanumeric characters.")
        return

    if (VOICE_DIR / f"{voice_name}.wav").exists():
        await update.message.reply_text(
            f"A voice named '{voice_name}' already exists. Choose a different name."
        )
        return

    awaiting_voice_upload[user_id] = voice_name
    await update.message.reply_text(
        f"Okay, preparing to add new voice: '{voice_name}'.\n"
        f"Please send the voice recording now (as a voice message or a .wav audio file)."
    )

# --- Callback Query Handler ---

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer() # Acknowledge the button press

    user_id = query.from_user.id
    data = query.data

    if data.startswith(CALLBACK_PREFIX_VOICE):
        voice_name = data[len(CALLBACK_PREFIX_VOICE):]
        user_selected_voice[user_id] = voice_name
        await query.edit_message_text(text=f"Voice set to '{voice_name}'.")
    else:
        logger.warning(f"Unknown callback data: {data}")
        await query.edit_message_text(text="Sorry, an unknown action occurred.")


# --- Message Handlers ---

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    text = update.message.text

    if user_id in awaiting_voice_upload:
        # User might have typed text instead of sending audio after /newvoice
        voice_name_pending = awaiting_voice_upload[user_id]
        await update.message.reply_text(
            f"I'm currently waiting for an audio file for the voice '{voice_name_pending}'.\n"
            "Please send a voice message or a .wav file. If you want to cancel, use /cancel (not implemented yet), or just send text after a while."
        )
        return

    # Determine voice: user's selection, or default, or first available
    selected_voice = user_selected_voice.get(user_id)
    if not selected_voice:
        available_voices = get_available_voices()
        if available_voices:
            selected_voice = available_voices[0] # Use first available as a fallback
            logger.info(f"User {user_id} has no selection, using first available: {selected_voice}")
        else:
            selected_voice = DEFAULT_VOICE_NAME # Fallback to a conceptual default
            logger.info(f"User {user_id} has no selection, no voices available, using conceptual default: {selected_voice}")

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.RECORD_VOICE)
    try:
        audio_data = await tts(selected_voice, text)
        await update.message.reply_voice(voice=audio_data, caption=f"Voice: {selected_voice}")
    except Exception as e:
        logger.error(f"Error during TTS or sending voice for user {user_id}: {e}")
        await update.message.reply_text("Sorry, I couldn't generate the speech for that text.")

async def handle_audio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id

    if user_id not in awaiting_voice_upload:
        # If user sends a voice message without /newvoice command
        await update.message.reply_text(
            "If you want to add this as a new voice, please use the /newvoice <name> command first."
        )
        return

    voice_name = awaiting_voice_upload.pop(user_id) # Retrieve and remove from pending
    
    # Telegram voice messages are .oga (Opus). Documents can be .wav
    # We'll save it as .wav extension, but actual content might be oga.
    # A real TTS might need conversion (e.g. ffmpeg) if it only supports wav.
    
    audio_message_part = update.message.voice or update.message.audio # handles both voice notes and audio files
    
    if not audio_message_part:
        logger.warning(f"User {user_id} was awaiting voice upload for '{voice_name}' but sent no audio.")
        await update.message.reply_text("Something went wrong, I didn't receive an audio file. Please try /newvoice again.")
        return

    # Check mime type if it's a document, to be more specific for .wav
    if update.message.document and update.message.document.mime_type not in ["audio/wav", "audio/x-wav", "audio/wave"]:
        logger.info(f"User {user_id} sent document for '{voice_name}' but not WAV: {update.message.document.mime_type}")
        # You could reject here, or try to process it anyway. For now, we'll proceed.
        # await update.message.reply_text("Please send a .wav file or a voice message.")
        # awaiting_voice_upload[user_id] = voice_name # Put it back if rejecting
        # return

    try:
        file_id = audio_message_part.file_id
        new_file = await context.bot.get_file(file_id)
        
        file_path = VOICE_DIR / f"{voice_name}.wav" # Save with .wav extension
        VOICE_DIR.mkdir(parents=True, exist_ok=True) # Ensure directory exists

        await new_file.download_to_drive(custom_path=file_path)
        logger.info(f"New voice '{voice_name}' from user {user_id} saved to {file_path}")
        await update.message.reply_text(f"New voice '{voice_name}' added successfully!")
    except Exception as e:
        logger.error(f"Failed to download/save voice for '{voice_name}' from user {user_id}: {e}")
        await update.message.reply_text(
            f"Sorry, there was an error saving the voice '{voice_name}'. Please try again."
        )
        # Potentially re-add to awaiting_voice_upload if it's a retryable error
        # awaiting_voice_upload[user_id] = voice_name


# --- Main Bot Setup ---
def main():
    # Create voice directory if it doesn't exist
    VOICE_DIR.mkdir(parents=True, exist_ok=True)
    
    # It's good practice to provide a default.wav if your DEFAULT_VOICE_NAME expects one
    # For example, create a dummy one if it doesn't exist:
    default_wav_path = VOICE_DIR / f"{DEFAULT_VOICE_NAME}.wav"
    if not default_wav_path.exists() and DEFAULT_VOICE_NAME != "default": # Avoid creating for "default" if it's purely conceptual
        try:
            with open(default_wav_path, "wb") as f:
                f.write(b"DUMMY_DEFAULT_WAV_CONTENT") # placeholder
            logger.info(f"Created dummy default voice at {default_wav_path}")
        except OSError as e:
            logger.error(f"Could not create dummy default voice: {e}")


    application = Application.builder().token(BOT_TOKEN).build()

    # Command Handlers
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("voice", voice_command))
    application.add_handler(CommandHandler("newvoice", newvoice_command))

    # Callback Query Handler
    application.add_handler(CallbackQueryHandler(button_handler))

    # Message Handlers
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    # Handle both direct voice messages and audio files sent as documents
    application.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, handle_audio))


    logger.info("Bot starting...")
    application.run_polling()

if __name__ == "__main__":
    main()