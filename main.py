# Load environment variables first
from dotenv import load_dotenv
load_dotenv()

# Flask web framework modules
from flask import Flask, request, jsonify, render_template, redirect, session, url_for, flash, send_from_directory

# Timing module (measures model response time)
import time

# Logging module
import logging

# Read environment variables
import os
import secrets
import re
import json
import random
import base64
import threading
import tempfile
from urllib.parse import urlparse

# Used to run multiple provider requests concurrently
from concurrent.futures import ThreadPoolExecutor

# Configure log level
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Create the Flask app
app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', secrets.token_hex(32))

from auth import auth_bp  # noqa: E402
app.register_blueprint(auth_bp)

from auth.db import (  # noqa: E402
    save_chat_history,
    get_chat_history_list,
    get_chat_history_by_id,
    delete_chat_history,
    update_chat_history_title,
    toggle_pin_chat_history,
    append_chat_history_result,
    update_chat_history_peer_reviews,
    save_image_history,
    get_image_history_list,
    get_image_history_by_id,
    delete_image_history,
    update_image_history_title,
    toggle_pin_image_history,
    append_image_history_result,
    get_claude_free_tier_usage,
    increment_claude_free_tier_usage,
    decrement_claude_free_tier_usage,
    get_gemini_free_tier_usage,
    increment_gemini_free_tier_usage,
    decrement_gemini_free_tier_usage,
    get_free_tier_usage,
    increment_free_tier_usage,
    decrement_free_tier_usage,
)


# =========================
# Initialize g4f providers
# =========================
try:
    import g4f
    from g4f.client import Client as G4FImageClient
    import g4f.image.copy_images as _g4f_copy_images

    # 2026-07-09: on GAE Standard (gen1/gen2 alike, python312 is no exception), the local
    # filesystem is read-only everywhere except /tmp. g4f's own image download defaults to
    # its own module-level relative paths './generated_images'/'./generated_media'; the CWD
    # is writable in local dev, which masks the problem, but mkdir/open always fails once
    # deployed to production -- this isn't "disk occasionally full," it's every single time.
    # The Gemini/ChatGPT image results saved here and the free images g4f downloads itself
    # go through the same get_media_dir() and these same two module-level variables, so we
    # redirect them straight to tempfile.gettempdir() (the actually-writable /tmp on GAE),
    # fixing both sides with one change; serve_generated_media() reads the same
    # get_media_dir(), so reads and writes stay in sync automatically with no extra change.
    _g4f_copy_images.images_dir = os.path.join(tempfile.gettempdir(), 'generated_images')
    _g4f_copy_images.media_dir = os.path.join(tempfile.gettempdir(), 'generated_media')
    from g4f.image.copy_images import get_media_dir

    G4F_AVAILABLE = True
    logger.info("g4f imported successfully")

    # Currently supported provider list. CohereForAI_C4AI_Command was added 2026-07-05 after
    # an availability survey (see availability_g4f/available_providers_models.txt), with zero
    # failures across several rounds of real calls. Groq/OpenRouterFree were removed after the
    # 2026-07-05 GAE deploy: both return "Error 403: Access from cloud provider blocked" 100%
    # of the time from a cloud provider environment (g4f's own active block on cloud IPs, see
    # g4f.dev/members.html); local environments are unaffected, but production is entirely
    # unusable, and there is no free, key-free way around that block, so they were dropped
    # outright rather than kept as an option.
    G4F_PROVIDERS = [
        g4f.Provider.Yqcloud,
        g4f.Provider.OperaAria,
        g4f.Provider.PollinationsAI,
        g4f.Provider.CohereForAI_C4AI_Command,
    ]

    # Config mapping table: one provider maps to one model list.
    # The first model in the list is treated as that provider's default model.
    PROVIDER_MODELS_MAP = {
        'Yqcloud': ['gpt-3.5-turbo', 'gpt-4'],
        'OperaAria': ['aria'],
        'PollinationsAI': ['openai-fast'],
        'CohereForAI_C4AI_Command': ['command-a-03-2025', 'command-r-08-2024'],
    }

    # Text-to-image provider list: the 2026-07-05 retest (see
    # availability_g4f/available_free_image_providers.txt) removed BlackForestLabs_Flux1Dev
    # and StabilityAI_SD35Large -- both share HuggingFace's free ZeroGPU quota pool under the
    # hood, and hit "You have exceeded your ZeroGPU quota (0s left)" 100% of the time across
    # 4 consecutive rounds of real testing; the quota has been globally exhausted with no
    # short-term recovery, and this same retest round found no other usable, key-free
    # replacement provider (see the full candidate list and failure reasons in the same
    # directory's available_image_providers_models.txt). The remaining 3 providers all go
    # through g4f.client.Client().images.generate() (a completely separate g4f interface from
    # the text chat g4f.ChatCompletion.create() above; the two must not be mixed).
    IMAGE_PROVIDERS = [
        g4f.Provider.PollinationsImage,
        g4f.Provider.AnyProvider,
        g4f.Provider.OperaAria,
    ]

    # Text-to-image provider -> model mapping table. 'auto' is PollinationsImage's placeholder
    # display value; when selected, images.generate() is called with no model parameter (it
    # falls back to its own default_image_model internally).
    IMAGE_PROVIDER_MODELS_MAP = {
        'PollinationsImage': ['auto'],
        'AnyProvider': ['flux'],
        'OperaAria': ['aria'],
    }

    # Invisible prompt routing table: (provider_name, model) -> a style prompt appended to the
    # end of the user's prompt. Design principle: the first sentence must carry an "act now"
    # urgency instruction (to prevent timeouts); beyond that, it should bring out each model's
    # genuine personality. Reworded slightly on 2026-07-07 alongside the frontier model
    # personas, to make sure these 4 free personas still read as clearly distinct sitting
    # next to the 6 new frontier personas (see FRONTIER_STYLE_PROMPTS_MAP below); the word
    # count limits/structure requirements themselves are unchanged (edits here get overridden
    # by test content injected via patch() in test_main_whitebox.py etc., so they don't depend
    # on the exact strings below):
    # gpt-4              -> a rigorous analyst who never strays from the evidence trail:
    #                       conclusion-evidence-reflection three-part structure, 300 words
    # gpt-3.5            -> a never-rambles efficiency type: TLDR one-sentence conclusion
    #                       first, conversational tone, 150 words
    # aria               -> a hands-on consultant who trusts action over analysis: skip the
    #                       preamble, give 1-2 actionable steps directly, 200 words
    # openai-fast        -> a word-frugal, ultra-fast answerer: one sentence conclusion + one
    #                       sentence reason, English output, under 100 words
    # command-a-03-2025  -> a business consultant grounded in Cohere's enterprise focus:
    #                       structured bullet points, geared toward actionable decisions, 250
    #                       words
    # command-r-08-2024  -> a fact-checker grounded in Cohere's retrieval-augmented focus:
    #                       leads with verifiable facts, flags uncertainty, 200 words
    ROUTE_PROMPTS_MAP = {
        ('Yqcloud', 'gpt-4'): '\n\n[System: Respond immediately. You are a rigorous analyst who never states a conclusion without showing its evidence trail. Answer quickly using a three-part structure: "Core conclusion -> Key evidence -> Potential risks or reflection." Keep the entire response under 300 words.]',
        ('Yqcloud', 'gpt-3.5-turbo'): '\n\n[System: Give a TLDR immediately. You are a no-nonsense efficiency assistant who leads with the punchline and never over-explains. State the single most important conclusion in one sentence first, then add up to two key points. Reply in a casual, conversational tone. Keep the entire response under 150 words. No filler.]',
        ('OperaAria', 'aria'): '\n\n[System: Give actionable advice immediately. You are a hands-on consultant who trusts action over analysis. Skip the background and tell the user directly "here are the 1-2 things you can do right now," tailored to the current situation. Keep the entire response under 200 words.]',
        ('PollinationsAI', 'openai-fast'): '\n\n[System: Reply immediately. You are a speed-first minimalist who never wastes a word. Give ONE sentence answer then ONE sentence reason. English only. Max 100 words. No preamble.]',
        ('CohereForAI_C4AI_Command', 'command-a-03-2025'): '\n\n[System: Respond immediately. You are an enterprise business consultant in the spirit of Cohere\'s enterprise-AI focus, structuring answers around what a decision-maker can act on. Lead with a short structured breakdown of options or steps, then a clear recommendation. Keep the entire response under 250 words.]',
        ('CohereForAI_C4AI_Command', 'command-r-08-2024'): '\n\n[System: Respond immediately. You are a fact-checking researcher in the spirit of Cohere\'s retrieval-augmented-generation focus. Lead with the most verifiable factual points, and explicitly flag anything you are not certain about. Keep the entire response under 200 words.]',
    }

    # Peer review judge prompt config table: model -> a dedicated judge prompt prefix
    # (requires JSON output). The frontier models' judge personas (keyed by their own
    # model_key, e.g. 'claude-sonnet-5') are appended via PEER_REVIEW_PROMPTS_MAP.update() at
    # the FRONTIER_STYLE_PROMPTS_MAP definition below, not written inside this g4f-only try
    # block -- the except branch here resets the whole dict to {}, and the frontier models'
    # personas should not degrade along with g4f's availability.
    PEER_REVIEW_PROMPTS_MAP = {
        'gpt-4': 'You are now a blind review judge. Rigorously examine the following anonymous answer and point out any logical gaps, factual errors, or insufficiently supported arguments. Output ONLY this JSON, nothing else: {"score": integer(1-100), "comment": "one sharp sentence critique"}',
        'gpt-3.5-turbo': 'Quickly assess the following anonymous answer for organization and readability. Output ONLY this JSON, nothing else: {"score": integer(1-100), "comment": "one efficiency-focused sentence of editing feedback"}',
        'aria': 'Review the following anonymous answer from a practical standpoint, noting how down-to-earth and actionable it is. Output ONLY this JSON, nothing else: {"score": integer(1-100), "comment": "one blunt, plain-spoken sentence"}',
        'openai-fast': 'You are a blind reviewer. Rate the following answer for clarity and accuracy. Output ONLY this JSON, nothing else: {"score": integer(1-100), "comment": "one sharp sentence critique in English"}',
        'command-a-03-2025': 'You are a blind reviewer judging from a business-decision standpoint. Rate how actionable and well-structured the following answer is. Output ONLY this JSON, nothing else: {"score": integer(1-100), "comment": "one sentence critique focused on actionability"}',
        'command-r-08-2024': 'You are a blind reviewer judging factual accuracy. Rate how well-supported and verifiable the following answer is. Output ONLY this JSON, nothing else: {"score": integer(1-100), "comment": "one sentence critique focused on factual rigor"}',
    }

except ImportError as e:
    G4F_AVAILABLE = False
    G4F_PROVIDERS = []
    PROVIDER_MODELS_MAP = {}
    ROUTE_PROMPTS_MAP = {}
    PEER_REVIEW_PROMPTS_MAP = {}
    IMAGE_PROVIDERS = []
    IMAGE_PROVIDER_MODELS_MAP = {}
    G4FImageClient = None
    get_media_dir = lambda: os.path.join(tempfile.gettempdir(), 'generated_media')
    logger.warning(f"g4f not available: {e}")

except Exception as e:
    G4F_AVAILABLE = False
    G4F_PROVIDERS = []
    PROVIDER_MODELS_MAP = {}
    ROUTE_PROMPTS_MAP = {}
    PEER_REVIEW_PROMPTS_MAP = {}
    IMAGE_PROVIDERS = []
    IMAGE_PROVIDER_MODELS_MAP = {}
    G4FImageClient = None
    get_media_dir = lambda: os.path.join(tempfile.gettempdir(), 'generated_media')
    logger.warning(f"g4f initialization failed: {e}")


# =========================
# Initialize the official Anthropic (Claude) SDK
# A third call chain, fully independent from the g4f initialization above -- it is neither
# g4f.ChatCompletion.create() nor g4f's images.generate(), but calls Anthropic's own API
# endpoint directly through the official `anthropic` library. CLAUDE_AVAILABLE is a global
# boolean flag independent from G4F_AVAILABLE; either one being missing/failing to init only
# degrades its own feature, with no effect on the other. Claude also has its own independent
# provider/model namespace (CLAUDE_MODELS), not mixed with
# PROVIDER_MODELS_MAP/IMAGE_PROVIDER_MODELS_MAP.
# =========================
try:
    import anthropic

    CLAUDE_AVAILABLE = True
    logger.info("anthropic SDK imported successfully")
except ImportError as e:
    CLAUDE_AVAILABLE = False
    anthropic = None
    logger.warning(f"anthropic SDK not available: {e}")

# Mapping from model key (the frontend <select>'s value, also the display name/the `model`
# field in the request body) to the official API model ID. The key and the ID are currently
# one-to-one, but this mapping layer is kept so the frontend/request body never directly
# exposes the official, exact model ID string (only this one spot needs changing if the ID
# changes in the future).
CLAUDE_MODELS = {
    'claude-sonnet-5': 'claude-sonnet-5',
    'claude-haiku-4-5': 'claude-haiku-4-5-20251001',
}

# Non-streaming request; max_tokens is deliberately kept small (well below the official SDK's
# roughly 16000-token timeout-protection threshold for non-streaming requests), enough for a
# single short answer in this project's "compare multiple providers" scenario, with no need
# for streaming.
CLAUDE_MAX_TOKENS = 2048

# The cap on how many times a single registered user can call Claude for free against the
# developer account's quota, when they haven't brought their own key. One "Compare" click
# fires exactly one /api/claude-chat request no matter how many free, key-free g4f providers
# are also checked on the frontend, so it only consumes 1 quota unit -- this "one click = one
# quota unit" rule mirrors GEMINI_FREE_TIER_LIMIT (see CLAUDE.md section 6).
CLAUDE_FREE_TIER_LIMIT = 10


# =========================
# Initialize the official Google Gemini ("Nano Banana" series) image generation SDK
# A fourth call chain, fully independent and mirroring Claude's, but for the text-to-image
# scenario instead of chat: it does not go through g4f's images.generate() (the
# IMAGE_PROVIDERS namespace), but calls Gemini's image generation API directly through the
# official google-genai SDK. This is the first "paid/quota-limited" provider in this
# project's "text-to-image" scenario, with a free-quota/bring-your-own-key abuse-prevention
# mechanism fully mirroring Claude's (see call_gemini_image_model() and the
# /api/gemini-image route). GEMINI_AVAILABLE is independent from
# G4F_AVAILABLE/CLAUDE_AVAILABLE; either one being missing/failing to init only degrades its
# own feature.
#
# The package name is google-genai (PyPI), imported as `from google import genai` -- do not
# confuse it with the `google-generativeai` package (imported as `google.generativeai`) seen
# in earlier/other Google projects; that is a deprecated old SDK, not used in this project.
# =========================
try:
    from google import genai as google_genai

    GEMINI_AVAILABLE = True
    logger.info("google-genai SDK imported successfully")
except ImportError as e:
    GEMINI_AVAILABLE = False
    google_genai = None
    logger.warning(f"google-genai SDK not available: {e}")

# Mapping from model key (the frontend <select>'s value) to the official API model ID,
# mirroring CLAUDE_MODELS. All three tiers of the Nano Banana series (per the official docs
# at https://ai.google.dev/gemini-api/docs/models, checked 2026-07-04) are wired up (the
# Nano Banana 2/Lite tiers were added 2026-07-05; only Pro existed before that):
# - nano-banana-pro (gemini-3-pro-image): the flagship tier, "Professional design engine with
#   a reasoning core for studio-quality 4K visuals, complex layouts, and precise text
#   rendering", in the same "frontier" tier as Claude Sonnet 5 (the flagship model in
#   CLAUDE_MODELS).
# - nano-banana-2 (gemini-3.1-flash-image): mid tier, lighter weight/lower latency.
# - nano-banana-lite (gemini-3.1-flash-lite-image): the lightest tier.
# All three model IDs have been verified as valid via a direct call with a real
# GEMINI_API_KEY (see the comment above call_gemini_image_model() -- a real, zero-quota
# account gets the same 429 quota-exhausted error for all three, rather than the 404/400 that
# would appear for a nonexistent model, confirming all three pass model-name validation).
GEMINI_IMAGE_MODELS = {
    'nano-banana-pro': 'gemini-3-pro-image',
    'nano-banana-2': 'gemini-3.1-flash-image',
    'nano-banana-lite': 'gemini-3.1-flash-lite-image',
}

# The cap on how many times a single registered user can call Gemini image generation for
# free against the developer account's quota, when they haven't brought their own key.
# Mirrors CLAUDE_FREE_TIER_LIMIT, counted independently (the two quotas share nothing). One
# generate click fires exactly one /api/gemini-image request no matter which tier
# geminiModelSelect has selected, so it only consumes 1 quota unit -- this "one click = one
# quota unit" rule does not change with the number of model tiers (see CLAUDE.md section 6).
GEMINI_FREE_TIER_LIMIT = 10

# Gemini model mapping for the chat scenario (added 2026-07-06): mirrors GEMINI_IMAGE_MODELS
# in structure but is fully independent in fields/quota -- Gemini is now two parallel
# frontier providers in this project: "Gemini image" (the set above, type='google_genai') and
# "Gemini text" (call_gemini_text_model(), type='google_genai_text'), which appears in the
# chat form alongside Claude/ChatGPT, going through its own independent /api/gemini-chat
# route and its own independent quota counter gemini_text_free_tier_usage, sharing nothing
# with the image quota gemini_free_tier_usage.
GEMINI_TEXT_MODELS = {
    'gemini-3.5-flash': 'gemini-3.5-flash',
    'gemini-3.1-flash-lite': 'gemini-3.1-flash-lite',
}
GEMINI_TEXT_FREE_TIER_LIMIT = 10
GEMINI_TEXT_FREE_TIER_FIELD = 'gemini_text_free_tier_usage'


# Error classification logic extracted from call_gemini_image_model() (defined below), shared
# with call_gemini_text_model() -- both call the same google-genai SDK, so exceptions take
# the same shape.
def _classify_google_genai_error(e):
    status_code = getattr(e, 'status_code', None) or getattr(e, 'code', None)
    status_str = (getattr(e, 'status', None) or '').upper()
    message = getattr(e, 'message', None) or str(e)

    if status_code == 429 or status_str == 'RESOURCE_EXHAUSTED':
        return 'QUOTA_EXHAUSTED', message
    if status_code == 403:
        return 'PERMISSION_DENIED', message
    # Live-verified 2026-08-28 (real call against the deployed google-genai version with a
    # deliberately invalid key): an invalid API key does NOT come back as the 403 the
    # troubleshooting docs imply -- it raises a private-module BadRequestError with
    # status_code == 400 and a message embedding "'message': 'API key not valid. Please pass
    # a valid API key.', 'status': 'INVALID_ARGUMENT'" plus an ErrorInfo reason of
    # API_KEY_INVALID. Without this branch that shape fell through to the generic
    # passthrough, showing the raw JSON blob to the user instead of the invalid-key message.
    # Checked after the 429/403 branches so it can never shadow a quota signal.
    if 'api key not valid' in message.lower() or 'api_key_invalid' in message.lower():
        return 'PERMISSION_DENIED', message
    if status_code is not None:
        return None, f'Error {status_code}: {message}'
    return None, message


# =========================
# Initialize the official OpenAI (ChatGPT) SDK
# A fifth independent call chain, serving both the chat scenario (call_chatgpt_model()) and
# the image generation scenario (call_chatgpt_image_model()) -- both share the same official
# openai SDK client construction approach and the same error classification
# (_classify_openai_error()), but have their own independent model mapping tables, quota
# constants, and routes, consistent with the existing "build an independent set per frontier
# provider" pattern used for Claude/Gemini. CHATGPT_AVAILABLE is independent from
# G4F_AVAILABLE/CLAUDE_AVAILABLE/GEMINI_AVAILABLE; if missing, only ChatGPT's own two routes
# degrade.
# =========================
try:
    import openai

    CHATGPT_AVAILABLE = True
    logger.info("openai SDK imported successfully")
except ImportError as e:
    CHATGPT_AVAILABLE = False
    openai = None
    logger.warning(f"openai SDK not available: {e}")

# ChatGPT model mapping for the chat scenario, mirroring CLAUDE_MODELS.
CHATGPT_MODELS = {
    'gpt-5.5': 'gpt-5.5',
    'gpt-5.4-mini': 'gpt-5.4-mini',
}
CHATGPT_MAX_TOKENS = 2048
CHATGPT_FREE_TIER_LIMIT = 10
CHATGPT_FREE_TIER_FIELD = 'chatgpt_free_tier_usage'

# ChatGPT model mapping for the image generation scenario, mirroring GEMINI_IMAGE_MODELS,
# with its own independent quota.
CHATGPT_IMAGE_MODELS = {
    'gpt-image-2': 'gpt-image-2',
    'gpt-image-1.5': 'gpt-image-1.5',
}
CHATGPT_IMAGE_FREE_TIER_LIMIT = 10
CHATGPT_IMAGE_FREE_TIER_FIELD = 'chatgpt_image_free_tier_usage'


# Error classification shared by call_chatgpt_model()/call_chatgpt_image_model(): same openai
# SDK, so exceptions take the same shape. 'insufficient_quota' is the quota-exhausted error
# code documented in OpenAI's official docs, not yet verified against a real exhausted
# account (the same type of verification gap Gemini started with, see CLAUDE.md).
def _classify_openai_error(e):
    status_code = getattr(e, 'status_code', None)
    err_code = getattr(e, 'code', None)
    message = getattr(e, 'message', None) or str(e)

    is_quota_exhausted = (
        err_code == 'insufficient_quota'
        or 'insufficient_quota' in message.lower()
        or 'exceeded your current quota' in message.lower()
    )
    if is_quota_exhausted:
        return 'QUOTA_EXHAUSTED', message
    if status_code == 401:
        return 'PERMISSION_DENIED', message
    if status_code is not None:
        return None, f'Error {status_code}: {message}'
    return None, message


# Frontier model invisible prompt routing table, mirroring ROUTE_PROMPTS_MAP in structure
# (first-sentence urgency instruction + a persona grounded in the real company's philosophy +
# a word cap), keyed by each model's own model_key (not a (provider, model) tuple -- each
# frontier provider's call maps to exactly one model, so there is no g4f-style "same provider,
# multiple models" ambiguity):
# - claude-sonnet-5/claude-haiku-4-5 -> Anthropic's helpful-honest-harmless tradition:
#   admitting uncertainty beats faking confidence; sonnet is the deep, deliberate thinker,
#   haiku is the fast-answer version under the same honesty standard.
# - gpt-5.5/gpt-5.4-mini -> OpenAI's "for everyone" general-assistant positioning: 5.5 is the
#   well-rounded generalist, 5.4-mini is the same generalist's fast, no-preamble version.
# - gemini-3.5-flash/gemini-3.1-flash-lite -> Google's tradition of organizing
#   information/native multimodality: flash is a high-density fact synthesizer, flash-lite is
#   the minimal-latency version that gives only the necessary conclusion.
FRONTIER_STYLE_PROMPTS_MAP = {
    'claude-sonnet-5': '\n\n[System: Respond thoughtfully but promptly. You are a careful, nuanced reasoner in the Anthropic tradition of helpful, honest, and harmless AI. Weigh the meaningful angles of the question, and explicitly flag genuine uncertainty rather than projecting false confidence. Keep the response well-structured and under 300 words.]',
    'claude-haiku-4-5': '\n\n[System: Respond immediately. You are a fast reasoner who still holds the same honesty standard as your larger sibling model -- never trade accuracy for speed, and say so plainly when you are unsure. Keep the response concise, under 180 words.]',
    'gpt-5.5': '\n\n[System: Respond immediately. You are a versatile, broadly capable generalist assistant in the OpenAI tradition of building useful AI for everyone. Cover the practical breadth of the question with clear structure -- conclusion first, then supporting detail. Keep the entire response under 300 words.]',
    'gpt-5.4-mini': '\n\n[System: Respond immediately. You are the fast, no-preamble version of a broad generalist assistant. Get straight to the useful answer, skip throat-clearing. Keep the entire response under 150 words.]',
    'gemini-3.5-flash': '\n\n[System: Respond immediately. You are an information-dense synthesizer in the Google tradition of organizing knowledge -- connect the relevant facts efficiently and structure the answer so it can be scanned quickly. Keep the entire response under 220 words.]',
    'gemini-3.1-flash-lite': '\n\n[System: Respond immediately. You are the lowest-latency responder -- give only the essential answer with no elaboration or hedging. Keep the entire response under 100 words.]',
}

# Frontier models' peer review judge personas, keyed by model_key (no collision with g4f's
# use of model name as key, since the strings themselves don't overlap). Each persona keeps
# the same "personality" as that same model's answering persona in FRONTIER_STYLE_PROMPTS_MAP,
# just switched from "how to answer" to "how to judge others": Claude focuses on honesty/
# overconfidence, ChatGPT focuses on practical breadth and completeness, Gemini focuses on
# factual density and how well-structured the answer is. Merged into PEER_REVIEW_PROMPTS_MAP
# via .update(), not written inside the g4f try/except block above, so that even if g4f is
# unavailable (those two except branches reset PEER_REVIEW_PROMPTS_MAP to {}), the frontier
# models' judge personas still exist -- the two have independent availability.
FRONTIER_JUDGE_PROMPTS_MAP = {
    'claude-sonnet-5': 'You are a meticulous reviewer in the Anthropic tradition: rigorously check the following anonymous answer for honesty, nuance, and unacknowledged uncertainty -- treat overconfidence as a real flaw, not a strength. Output ONLY this JSON, nothing else: {"score": integer(1-100), "comment": "one nuanced sentence noting a genuine strength or honesty gap"}',
    'claude-haiku-4-5': 'Quickly but carefully check the following anonymous answer for factual overreach or false confidence. Output ONLY this JSON, nothing else: {"score": integer(1-100), "comment": "one concise, calibrated sentence"}',
    'gpt-5.5': 'You are a broad generalist reviewer. Assess the following anonymous answer for practical usefulness and completeness across the full scope of the question. Output ONLY this JSON, nothing else: {"score": integer(1-100), "comment": "one sentence on practical completeness"}',
    'gpt-5.4-mini': 'Quickly assess the following anonymous answer for efficiency and directness. Output ONLY this JSON, nothing else: {"score": integer(1-100), "comment": "one efficiency-focused sentence"}',
    'gemini-3.5-flash': 'Assess the following anonymous answer for factual density and how well it is organized for fast scanning, in the tradition of structured knowledge synthesis. Output ONLY this JSON, nothing else: {"score": integer(1-100), "comment": "one sentence on factual grounding or structure"}',
    'gemini-3.1-flash-lite': 'Rate the following anonymous answer purely on brevity and essential-content ratio -- penalize padding. Output ONLY this JSON, nothing else: {"score": integer(1-100), "comment": "one terse sentence"}',
}
PEER_REVIEW_PROMPTS_MAP.update(FRONTIER_JUDGE_PROMPTS_MAP)


# ==================================================
# Helper function: determine the final model to use, from the mapping table and the
# user's request.
# Rule A: the user-requested model is in the supported list -> use it directly.
# Rule B: unsupported or not specified -> fall back to that provider's default model
#         (the first one in the list).
# Rule C: the provider has no model config at all -> fall back to "gpt-3.5-turbo".
# ==================================================
def determine_actual_model(provider_name, requested_model):
    supported_models = PROVIDER_MODELS_MAP.get(provider_name, [])
    if requested_model in supported_models:
        return requested_model
    return supported_models[0] if supported_models else "gpt-3.5-turbo"


# ==================================================
# Helper function: initialize the standard result dict.
# Manages the key set in one place, keeping the normal flow and the fallback-on-error
# structure strictly consistent.
# ==================================================
def init_result_object(provider_name, model):
    return {
        'provider': provider_name,
        'success': False,
        'response': '',
        'error': '',
        'response_time': 0,
        'model': model,
        'type': 'g4f'
    }


# ==================================================
# Helper function: determine the final image model to use, from the text-to-image mapping
# table and the user's request.
# Mirrors determine_actual_model (rules A/B), but has no rule-C-style universal fallback
# model -- there is no default model name across text-to-image providers that always works;
# if the provider is not in the mapping table, it returns None directly, leaving it to the
# caller to decide how to display that (see init_image_result_object).
# ==================================================
def determine_actual_image_model(provider_name, requested_model):
    supported_models = IMAGE_PROVIDER_MODELS_MAP.get(provider_name, [])
    if requested_model in supported_models:
        return requested_model
    return supported_models[0] if supported_models else None


# ==================================================
# Helper function: initialize the standard image result dict.
# Similar in structure to the text 7-key contract (init_result_object) but with different
# fields: the url/b64_json fields each carry one of the two possible return shapes of g4f
# ImagesResponse.data[0], instead of a single response string field; the frontend picks
# whichever is present to render <img src="...">.
# ==================================================
def init_image_result_object(provider_name, model):
    return {
        'provider': provider_name,
        'success': False,
        'url': None,
        'b64_json': None,
        'error': '',
        'response_time': 0,
        'model': model,
        'type': 'g4f_image'
    }


# ==================================================
# Helper function: scan the text for every top-level, brace-balanced {...} candidate
# substring.
# Some reviewers (especially reasoning models) sandwich a self-corrected draft around the
# final JSON, so more than one {...} shape can appear in the text. The old implementation
# used text.find('{')/text.rfind('}') to trim head and tail; whenever two JSON blobs
# appeared, it would scoop up the draft text in between too, stitching together an
# unparseable string, so the whole thing failed to parse and fell back to the 80-point
# default -- but the draft text preserved verbatim in that fallback text happened to contain
# another valid score, so the user would see "80 points" not matching the score inside the
# comment. Switched to scanning by brace-depth balancing, getting each independent, possibly
# valid candidate substring, and letting the caller try parsing them one by one.
# ==================================================
def _extract_balanced_json_candidates(text):
    candidates = []
    depth = 0
    start = None
    for i, ch in enumerate(text):
        if ch == '{':
            if depth == 0:
                start = i
            depth += 1
        elif ch == '}':
            if depth > 0:
                depth -= 1
                if depth == 0 and start is not None:
                    candidates.append(text[start:i + 1])
                    start = None
    return candidates


# ==================================================
# Helper function: parse a peer review JSON response, extracting score and comment.
# Fault-tolerance strategy: try parsing starting from the last candidate JSON (after a
# model's self-correction, the final version is usually last); the first candidate that
# parses successfully and has a valid numeric score is used; only if every candidate fails
# to parse does it fall back to a default score of 80 + the raw text as the comment.
# ==================================================
def parse_peer_review_json(text):
    for candidate in reversed(_extract_balanced_json_candidates(text)):
        try:
            data = json.loads(candidate)
        except Exception:
            continue
        score = data.get('score')
        if isinstance(score, (int, float)):
            comment = str(data.get('comment', ''))
            return max(1, min(100, int(score))), comment
    return 80, text.strip()


# Blocked keyword list (currently an empty placeholder list; fill in keywords as needed to
# activate it)
SENSITIVE_KEYWORDS = []

# Network/rate-limit error keywords: on a match, return a uniform "system busy" friendly
# message instead of the raw exception text
NETWORK_ERROR_KEYWORDS = [
    'timeout', 'timed out', 'connection', 'network', 'remote',
    '502', '504', 'rate limit', 'too many requests', 'unavailable',
    'ssl', 'broken pipe', 'connection reset',
]

# The peer review stage's network-error check additionally counts 429/queue-full (used for
# the fallback message once retries are exhausted)
PEER_REVIEW_NETWORK_ERROR_KEYWORDS = NETWORK_ERROR_KEYWORDS + ['429', 'queue']

# Content policy error keywords: on a match, this means the provider's underlying vendor
# (e.g. Azure OpenAI) blocked the response with its own content moderation; retrying is
# pointless, so this needs to be distinguished from network errors and given its own
# friendly message
CONTENT_POLICY_ERROR_KEYWORDS = [
    'content management policy',
    'content_filter',
    'content filtering polic',
    'response was filtered',
    'responsible ai',
]

# GPU quota error keywords (text-to-image only, hit by HuggingFace ZeroGPU Space backends
# like BlackForestLabs_Flux1Dev/StabilityAI_SD35Large): on a match, this means the free GPU
# quota has been exhausted, a resource limit of that provider itself rather than network
# jitter -- distinguished from network errors and given its own friendly message, otherwise
# the frontend would show the raw English JSON error directly (see test_g4f_image_provider)
GPU_QUOTA_ERROR_KEYWORDS = [
    'zerogpu',
    'gpu token limit',
    'gpu quota',
]


# ==================================================
# Helper function: detect repeated text and truncate it, while also filtering blocked content
# ==================================================
def detect_and_truncate(text):
    for kw in SENSITIVE_KEYWORDS:
        if kw in text:
            return "Content contains sensitive information and has been blocked."

    n = len(text)
    if n < 24:
        return text

    # --- Sentence-level repetition detection (split on sentence-ending punctuation or newline) ---
    parts = re.split(r'(?<=[。！？.!?\n])', text)
    parts = [p for p in parts if p.strip()]
    for i in range(len(parts) - 2):
        s = parts[i].strip()
        if s and s == parts[i + 1].strip() == parts[i + 2].strip():
            first_pos = text.find(parts[i])
            second_pos = text.find(parts[i + 1], first_pos + len(parts[i]))
            third_pos = text.find(parts[i + 2], second_pos + len(parts[i + 1]))
            if third_pos != -1:
                return text[:third_pos] + '... (truncated automatically due to repeated content)'

    # --- Sliding-window short-string repetition detection (8-50 char window, covers a short
    # phrase/sentence looping) ---
    for win in range(8, min(51, n // 3 + 1)):
        for i in range(n - win * 3 + 1):
            chunk = text[i:i + win]
            if (text[i + win:i + win * 2] == chunk and
                    text[i + win * 2:i + win * 3] == chunk):
                return text[:i + win * 2] + '... (truncated automatically due to repeated content)'

    return text


# ==================================================
# Test a single provider.
# Responsibilities:
# 1. Call the given provider
# 2. Dynamically match or validate the user-supplied model
# 3. Measure response time
# ==================================================
def test_g4f_provider(provider, prompt, requested_model=None):
    provider_name = provider.__name__
    actual_model = determine_actual_model(provider_name, requested_model)

    start_time = time.time()
    result = init_result_object(provider_name, actual_model)

    try:
        style_suffix = ROUTE_PROMPTS_MAP.get((provider_name, actual_model), '')
        routed_prompt = prompt + style_suffix

        # Call the model (pass the prompt with the invisible routing applied; no need to
        # modify the original prompt kept in result)
        response = g4f.ChatCompletion.create(
            model=actual_model,
            messages=[
                {
                    "role": "user",
                    "content": routed_prompt
                }
            ],
            provider=provider,
            timeout=20
        )

        result['success'] = True
        result['response'] = detect_and_truncate(str(response))

    except Exception as e:
        err_str = str(e).lower()
        if any(kw in err_str for kw in CONTENT_POLICY_ERROR_KEYWORDS):
            result['error'] = "This provider's content filter blocked the response. Try rephrasing your prompt."
        elif any(kw in err_str for kw in NETWORK_ERROR_KEYWORDS):
            result['error'] = 'The system is busy and trying to reconnect. Please try again shortly.'
        else:
            result['error'] = str(e)

    finally:
        result['response_time'] = round(
            time.time() - start_time,
            2
        )

    return result


# Text-to-image advisory timeout (the timeout kwarg passed to g4f images.generate(), not a
# hard cutoff). HuggingFace Space backends (BlackForestLabs_Flux1Dev/StabilityAI_SD35Large)
# have real cold-start/queueing delays, much slower than plain text chat, so this is set
# noticeably higher than the text path's 20s; the outer hard cutoff (see generate_images()'s
# future.result(timeout=...)) must leave a buffer, and the two must be adjusted together.
#
# The outer buffer formula is 2 * advisory + IMAGE_GENERATION_RETRY_SCHEDULING_BUFFER, not
# "advisory + a fixed small buffer" (the old formula before 2026-07-04, with a fixed 10s
# buffer). Reason: test_g4f_image_provider retries once on transient 429/queue errors, and
# both "how long the first attempt takes before throwing 429" and "how long the retried
# second attempt takes to succeed" can each run close to advisory_timeout before finishing
# (not the ideal case of "429 fails fast immediately") -- PollinationsImage once had its
# image actually generated and written to get_media_dir() after a 429 retry, but got
# discarded by future.result() as prematurely timed out because outer was only 10s wider
# than advisory. Each of the two attempts can independently eat the full advisory in the
# worst case, so outer must cover 2x advisory, not 1x advisory plus a small buffer. This is a
# generic timing issue with the retry mechanism itself (true for any provider that retries on
# 429), so it's fixed at the formula level, not by adding a one-off override for
# PollinationsImage.
IMAGE_GENERATION_ADVISORY_TIMEOUT = 40
IMAGE_GENERATION_RETRY_SCHEDULING_BUFFER = 5


def _compute_outer_timeout(advisory_timeout):
    return advisory_timeout * 2 + IMAGE_GENERATION_RETRY_SCHEDULING_BUFFER


IMAGE_GENERATION_OUTER_TIMEOUT = _compute_outer_timeout(IMAGE_GENERATION_ADVISORY_TIMEOUT)

# Per-provider advisory timeout override table: most image providers are fine with the
# default above, but AnyProvider is g4f's "aggregate and re-route" style provider -- it tries
# several real image backends internally in sequence until one succeeds or all are exhausted,
# taking noticeably longer with more variance, so it gets its own, more generous advisory
# budget. outer is not configured separately here -- it is always derived from advisory via
# _compute_outer_timeout(), ensuring the "both attempts can run the full advisory" retry
# buffer applies consistently to every provider (including AnyProvider with its overridden
# advisory). The outer timeout is computed independently per future, so it does not slow down
# the wait time for other providers in the same batch.
IMAGE_PROVIDER_TIMEOUT_OVERRIDES = {
    'AnyProvider': {'advisory': 70},
}


def get_image_timeouts(provider_name):
    override = IMAGE_PROVIDER_TIMEOUT_OVERRIDES.get(provider_name)
    advisory = override['advisory'] if override else IMAGE_GENERATION_ADVISORY_TIMEOUT
    return advisory, _compute_outer_timeout(advisory)


# ==================================================
# Test a single text-to-image provider.
# A fully separate call chain from test_g4f_provider():
# - Goes through g4f.client.Client().images.generate(), not g4f.ChatCompletion.create()
# - The return value follows the image 8-key contract (init_image_result_object), not the
#   text 7-key contract
# - Does not go through the ROUTE_PROMPTS_MAP invisible routing or the detect_and_truncate
#   repetition check -- both are designed for text answers and are meaningless for image
#   URL/base64 data
# ==================================================
def test_g4f_image_provider(provider, prompt, requested_model=None):
    provider_name = provider.__name__
    actual_model = determine_actual_image_model(provider_name, requested_model)
    display_model = actual_model or 'default'
    advisory_timeout, _ = get_image_timeouts(provider_name)

    start_time = time.time()
    result = init_image_result_object(provider_name, display_model)

    # A retry strategy mirroring run_peer_review(): only transient rate-limit errors like
    # 429/queue-full are worth retrying once; every other exception (including GPU quota
    # exhaustion, content policy, etc.) is pointless to retry, so break out immediately.
    for attempt in range(2):
        try:
            client = G4FImageClient()
            generate_kwargs = {
                'prompt': prompt,
                'provider': provider,
                'timeout': advisory_timeout,
            }
            # 'auto' (currently only used by PollinationsImage) means "don't specify a
            # concrete model, let the provider fall back to its own default_image_model", so
            # the model keyword argument is deliberately omitted.
            if actual_model and actual_model != 'auto':
                generate_kwargs['model'] = actual_model

            response = client.images.generate(**generate_kwargs)

            image_data = response.data[0] if response and response.data else None
            url = getattr(image_data, 'url', None) if image_data else None
            b64_json = getattr(image_data, 'b64_json', None) if image_data else None

            if url:
                result['success'] = True
                result['url'] = url
            elif b64_json:
                result['success'] = True
                result['b64_json'] = b64_json
            else:
                result['error'] = 'Provider returned no image data.'
            break

        except Exception as e:
            err_str = str(e).lower()
            if attempt == 0 and ('429' in err_str or 'queue' in err_str):
                wait = 2 + random.uniform(0, 1)
                logger.warning(
                    f"Image provider {provider_name} 429/queue, retrying in {wait:.1f}s"
                )
                time.sleep(wait)
                continue
            if any(kw in err_str for kw in GPU_QUOTA_ERROR_KEYWORDS):
                result['error'] = (
                    "This provider's free GPU quota is temporarily exhausted. "
                    "Try again later or pick a different provider."
                )
            # Uses PEER_REVIEW_NETWORK_ERROR_KEYWORDS (includes 429/queue) instead of
            # NETWORK_ERROR_KEYWORDS: like run_peer_review, this function retries once on
            # 429/queue, and once retries are exhausted these two error classes still need to
            # map to the "system busy" friendly message, instead of leaking the raw
            # "Error 429: ..." string to the frontend.
            elif any(kw in err_str for kw in PEER_REVIEW_NETWORK_ERROR_KEYWORDS):
                result['error'] = 'The system is busy and trying to reconnect. Please try again shortly.'
            else:
                result['error'] = str(e)
            break

    result['response_time'] = round(
        time.time() - start_time,
        2
    )

    return result


# Timeout budget + retry count for a single peer review request. A peer review that fails
# completely is now hidden entirely (no more "system busy" fallback text, see the end of
# run_peer_review()/run_frontier_peer_review()), so the only value of retrying is getting
# another shot at a real score, not avoiding an ugly fallback message; to prioritize keeping
# every provider's normal response from being slowed down by peer review, the retry count was
# cut from 3 down to 2 (1 retry), lowering the worst-case time for a single review.
# run_cross_peer_review()'s future timeout is derived from these two constants via a formula;
# don't change one without syncing the other (this same note is written next to both constant
# groups).
PEER_REVIEW_REQUEST_TIMEOUT = 25
PEER_REVIEW_MAX_ATTEMPTS = 2


def _peer_review_retry_wait(attempt):
    # attempt is "which retry is coming up next" (0-indexed, the first retry passes 0).
    # Backoff grows with the retry count (3-5s, 6-8s...), letting dense peer review requests
    # naturally spread out instead of all hitting the same rate-limit window that just
    # triggered a 429.
    return (attempt + 1) * 3 + random.uniform(0, 2)


def _peer_review_single_worst_case_seconds():
    # The worst-case upper bound on total time for a single peer review (run_peer_review's
    # whole retry chain): each attempt runs the full PEER_REVIEW_REQUEST_TIMEOUT, and the
    # backoff between retries is also counted at _peer_review_retry_wait()'s jitter upper
    # bound (+2s). run_cross_peer_review()'s future wait timeout is derived by multiplying
    # this value by the reviewer-level queue depth; it must not be a disconnected hardcoded
    # number.
    backoff_upper_bound_total = sum(
        (i + 1) * 3 + 2 for i in range(PEER_REVIEW_MAX_ATTEMPTS - 1)
    )
    return PEER_REVIEW_MAX_ATTEMPTS * PEER_REVIEW_REQUEST_TIMEOUT + backoff_upper_bound_total


# Fixed buffer for the future.result() wait, covering non-timing overhead like thread pool
# scheduling/GIL switching
PEER_REVIEW_FUTURE_TIMEOUT_BUFFER = 10


# ==================================================
# Helper function: run a single peer review request (does not go through the invisible
# prompt routing)
# ==================================================
def run_peer_review(reviewer_provider, reviewer_model, review_prompt):
    # On complete failure (retries exhausted or a non-retryable error), return None instead
    # of fallback text; the caller (run_cross_peer_review()) uses this to hide the whole
    # review entirely, instead of forcing a display of a fake score/fake comment like "system
    # busy" -- a successful response should not have its display experience dragged down just
    # because some free reviewer glitched.
    review_result = {
        'reviewer_provider': reviewer_provider.__name__,
        'reviewer_model': reviewer_model,
        'score': 80,
        'comment': '',
    }
    last_exc = None
    for attempt in range(PEER_REVIEW_MAX_ATTEMPTS):
        try:
            response = g4f.ChatCompletion.create(
                model=reviewer_model,
                messages=[{"role": "user", "content": review_prompt}],
                provider=reviewer_provider,
                timeout=PEER_REVIEW_REQUEST_TIMEOUT
            )
            score, comment = parse_peer_review_json(detect_and_truncate(str(response)))
            review_result['score'] = score
            review_result['comment'] = comment
            return review_result
        except Exception as e:
            last_exc = e
            err_str = str(e).lower()
            # Only 429/queue-full class errors are worth retrying; any other exception breaks out immediately
            if attempt < PEER_REVIEW_MAX_ATTEMPTS - 1 and ('429' in err_str or 'queue' in err_str):
                wait = _peer_review_retry_wait(attempt)
                logger.warning(
                    f"Peer review 429/queue from {reviewer_provider.__name__}, "
                    f"retrying in {wait:.1f}s (attempt {attempt + 2}/{PEER_REVIEW_MAX_ATTEMPTS})"
                )
                time.sleep(wait)
                continue
            break
    logger.warning(f"Peer review from {reviewer_provider.__name__} failed, hiding it: {last_exc}")
    return None


# ==================================================
# Frontier-model-specific peer review dispatch (added 2026-07-07). Forwards review_prompt to
# call_claude_model()/call_chatgpt_model()/call_gemini_text_model() (apply_persona=False; see
# the comment above that parameter inside each of these three functions for why: peer review
# uses the judge persona FRONTIER_JUDGE_PROMPTS_MAP, and should not also stack the "how to
# answer" persona suffix on top). The parsed score/comment is packaged into the exact same
# {reviewer_provider, reviewer_model, score, comment} shape as run_peer_review(), so
# run_cross_peer_review() can dispatch uniformly without needing to distinguish whether the
# reviewer is g4f or a frontier model. When user_api_key is non-empty, it is used for this
# review (independent of whether it's the same key that reviewer used to answer this round's
# prompt -- the two are routed independently, and the caller decides what to pass). On call
# failure (including developer account balance/quota exhaustion), returns None instead of a
# fallback review_result with a fake score, following the same "hide on failure" convention as
# run_peer_review() -- see the comment there.
# ==================================================
def run_frontier_peer_review(kind, model_key, review_prompt, user_api_key=None):
    call_fn = {
        'Claude': call_claude_model,
        'ChatGPT': call_chatgpt_model,
        'Gemini': call_gemini_text_model,
    }[kind]

    call_result = call_fn(review_prompt, model_key, user_api_key, apply_persona=False)

    if not call_result['success']:
        logger.warning(f"Peer review from {kind} failed, hiding it: {call_result.get('error')}")
        return None

    score, comment = parse_peer_review_json(call_result['response'])
    return {
        'reviewer_provider': kind,
        'reviewer_model': model_key,
        'score': score,
        'comment': comment,
    }


# ==================================================
# Unified cross g4f/frontier-model peer review scheduler (added 2026-07-07, replacing the
# peer review stage that used to be hardcoded inside compare_providers() and only covered the
# g4f namespace -- see the CLAUDE.md update log). entries is the set of results from this
# request that were judged successful and validated, each shaped as
# {'kind': 'g4f'|'Claude'|'ChatGPT'|'Gemini', 'provider': str, 'model': str,
#  'response': str, 'user_api_key': str|None}.
#
# Task-building rules fully mirror the old compare_providers(): every successful result is
# reviewed once by every other successful result, never by itself (determined by provider
# name; the same provider name only appears once per request). The only difference is that
# the reviewer/target can now come from either g4f or a frontier model; dispatch picks
# run_peer_review() (g4f, needs to map the provider name back to a g4f Provider class object)
# or run_frontier_peer_review() (frontier model) based on the reviewer's kind.
#
# Returns {provider_name: [review_item, ...]}; the caller (the /api/peer-review route) uses
# this to assemble each result's own peer_reviews array. When a reviewer fails completely,
# run_peer_review()/run_frontier_peer_review() returns None, and the None check before the
# append below drops that whole entry, so the array length can end up shorter than
# len(entries) - 1; the frontend already renders a variable-length array, so nothing needs to
# change there.
# ==================================================
def run_cross_peer_review(entries):
    g4f_provider_obj_map = {p.__name__: p for p in G4F_PROVIDERS}

    tasks = []
    for target in entries:
        for reviewer in entries:
            if reviewer['provider'] == target['provider']:
                continue
            judge_prefix = PEER_REVIEW_PROMPTS_MAP.get(
                reviewer['model'],
                'Please evaluate the quality of the following answer, noting its strengths and weaknesses.'
            )
            review_prompt = f"{judge_prefix}\n\nHere is the anonymous text to review:\n{target['response']}"
            tasks.append((reviewer, review_prompt, target['provider']))

    reviews_by_provider = {entry['provider']: [] for entry in entries}
    if not tasks:
        return reviews_by_provider

    # The more providers there are, the more targets each reviewer needs to review (N-1 of
    # them); these tasks get submitted to the thread pool below at the same time and are
    # very likely to land concurrently in the same time window -- this is the real cause of
    # the 429 storms seen against strictly rate-limited free backends like PollinationsAI
    # once provider count >= 6 (not an insufficient retry count, but the same reviewer being
    # hit by several concurrent requests right from the start). Here, each reviewer identity
    # (kind+provider) gets its own exclusive lock, serializing requests "aimed at the same
    # reviewer"; different reviewers still run concurrently through the thread pool, so this
    # does not degrade into the whole batch running fully serially.
    reviewer_task_counts = {}
    for reviewer, _, _ in tasks:
        key = (reviewer['kind'], reviewer['provider'])
        reviewer_task_counts[key] = reviewer_task_counts.get(key, 0) + 1
    reviewer_locks = {key: threading.Lock() for key in reviewer_task_counts}

    def _dispatch(reviewer, review_prompt):
        lock = reviewer_locks[(reviewer['kind'], reviewer['provider'])]
        with lock:
            if reviewer['kind'] == 'g4f':
                provider_obj = g4f_provider_obj_map.get(reviewer['provider'])
                if provider_obj is None:
                    return None
                return run_peer_review(provider_obj, reviewer['model'], review_prompt)
            return run_frontier_peer_review(
                reviewer['kind'], reviewer['model'], review_prompt, reviewer.get('user_api_key')
            )

    max_workers = min(10, len(tasks))
    # The worst-case time a single reviewer can be queued for = the number of tasks it must
    # handle serially (the max value in reviewer_task_counts) x the worst-case time for a
    # single peer review (_peer_review_single_worst_case_seconds()). The old value of 32s was
    # a constant set back when there was only 1 retry with a fixed 2-3s backoff, completely
    # disconnected from the retry count/backoff duration raised since then and the newly
    # added reviewer-level queueing, so it must be derived by formula, not kept as a hardcoded
    # number.
    max_reviewer_queue_depth = max(reviewer_task_counts.values())
    future_timeout = (
        max_reviewer_queue_depth * _peer_review_single_worst_case_seconds()
        + PEER_REVIEW_FUTURE_TIMEOUT_BUFFER
    )

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(_dispatch, reviewer, review_prompt): target_provider
            for reviewer, review_prompt, target_provider in tasks
        }
        for future, target_provider in futures.items():
            try:
                review_item = future.result(timeout=future_timeout)
                if review_item is not None:
                    reviews_by_provider[target_provider].append(review_item)
            except TimeoutError:
                logger.warning(f"Peer review for {target_provider} timed out after {future_timeout:.0f}s")
            except Exception as e:
                logger.error(f"Peer review error for {target_provider}: {e}", exc_info=True)

    return reviews_by_provider


# ==================================================
# Call the official Anthropic API to get Claude's answer.
# A third call chain, fully independent from test_g4f_provider()/test_g4f_image_provider():
# it does not go through g4f, does not participate in the ROUTE_PROMPTS_MAP invisible
# routing, and does not participate in peer review (run_peer_review only schedules within the
# providers_to_test/G4F_PROVIDERS namespace; Claude never appears there).
#
# Key routing rule (the core anti-abuse mechanism): when user_api_key is non-empty, it is
# used preferentially to instantiate the client, consuming none of the developer account's
# quota; the caller (the claude_chat route) uses this to decide whether it needs to
# check/increment the free quota counter -- this function itself has no knowledge of, and no
# concern for, the counter; it is only responsible for "which key to use for this request."
#
# Error classification: in real-world testing, an insufficient account balance returns 400 +
# error.type == "invalid_request_error" + a message containing "credit balance is too low"
# (not the originally assumed 429 + "insufficient_funds" -- that combination doesn't exist in
# Anthropic's error taxonomy; nor is it the 403 + "billing_error" hinted at literally by the
# general docs -- the real account tested here returns 400). The genuinely stable signal to
# check is the "credit balance" keyword in the message, not any specific status_code or
# error.type value -- 429 still specifically means rate_limit_error, a retryable transient
# error, a separate matter from balance exhaustion that should not be confused with it. The
# error.type == 'billing_error' branch is also kept as a compatibility fallback, in case some
# account/future API version really does take that more "documented" error shape.
# ==================================================

# Anthropic error classification, extracted from call_claude_model()'s inline
# anthropic.APIStatusError branch so all three frontier vendors expose the same
# _classify_*_error(e) -> (classification, message) shape and can be exercised by one shared
# fixture suite (tests/test_error_classifiers.py). Uses getattr() duck typing like the other
# two classifiers, so synthetic fixtures shaped like the real SDK exceptions classify
# identically to the live ones. Behavior is byte-for-byte what the inline branch did:
# "credit balance" in the message (the live-verified signal, see the comment above) or
# type == 'billing_error' (docs-derived fallback) -> SERVER_CREDITS_EXHAUSTED; 401 ->
# PERMISSION_DENIED with the fixed invalid-key message; anything else passes through as
# 'Error {status_code}: {message}'.
def _classify_anthropic_error(e):
    error_message = getattr(e, 'message', None) or str(e)
    is_credits_exhausted = (
        getattr(e, 'type', None) == 'billing_error'
        or 'credit balance' in error_message.lower()
    )
    if is_credits_exhausted:
        return 'SERVER_CREDITS_EXHAUSTED', error_message
    if getattr(e, 'status_code', None) == 401:
        return 'PERMISSION_DENIED', 'Invalid or missing Claude API key.'
    return None, f'Error {getattr(e, "status_code", None)}: {error_message}'


def call_claude_model(prompt, model_key, user_api_key=None, apply_persona=True):
    model_id = CLAUDE_MODELS.get(model_key)
    start_time = time.time()
    result = {
        'provider': 'Claude',
        'success': False,
        'response': '',
        'error': '',
        'response_time': 0,
        'model': model_key,
        'type': 'anthropic',
    }

    if not model_id:
        result['error'] = f'Unknown Claude model "{model_key}".'
        result['response_time'] = round(time.time() - start_time, 2)
        return result

    # apply_persona=False is for run_frontier_peer_review() reusing this function to fire a
    # peer review request -- the peer review's review_prompt already carries the judge
    # persona (FRONTIER_JUDGE_PROMPTS_MAP) and requires pure JSON output, and should not also
    # stack the "how to answer" persona suffix from FRONTIER_STYLE_PROMPTS_MAP on top, for the
    # same reason g4f's run_peer_review() never applies ROUTE_PROMPTS_MAP. Neither the
    # original prompt nor result is affected; only the content actually sent to the official
    # API gets the suffix appended.
    routed_prompt = prompt + FRONTIER_STYLE_PROMPTS_MAP.get(model_key, '') if apply_persona else prompt

    try:
        client = anthropic.Anthropic(api_key=user_api_key) if user_api_key else anthropic.Anthropic()
        response = client.messages.create(
            model=model_id,
            max_tokens=CLAUDE_MAX_TOKENS,
            messages=[{"role": "user", "content": routed_prompt}],
        )

        text = next((block.text for block in response.content if block.type == 'text'), '')
        result['response'] = detect_and_truncate(text)
        result['success'] = True

    except anthropic.APIStatusError as e:
        classification, message = _classify_anthropic_error(e)
        if classification == 'SERVER_CREDITS_EXHAUSTED':
            result['error'] = 'SERVER_CREDITS_EXHAUSTED'
            result['error_code'] = 'SERVER_CREDITS_EXHAUSTED'
        else:
            result['error'] = message

    except anthropic.APIConnectionError:
        result['error'] = 'The system is busy and trying to reconnect. Please try again shortly.'

    except Exception as e:
        result['error'] = str(e)

    finally:
        result['response_time'] = round(time.time() - start_time, 2)

    return result


# ==================================================
# Call the official Google Gemini ("Nano Banana") API to generate an image.
# A fully independent call chain from both test_g4f_image_provider() (g4f's IMAGE_PROVIDERS
# namespace) and call_claude_model(): it does not go through g4f, does not participate in the
# GPU_QUOTA_ERROR_KEYWORDS/PEER_REVIEW_NETWORK_ERROR_KEYWORDS checks or the 429/queue retry
# logic (that set is specific to g4f image providers), and does not participate in peer
# review (image generation itself has no concept of peer review). The return value shape is
# a second, independent implementation of the "image 8-key contract" -- same field
# names/meanings as g4f's init_image_result_object() (provider/success/url/b64_json/error/
# response_time/model/type), but with type='google_genai' instead of 'g4f_image', the same
# "similar field structure but independent type marker" relationship as the Claude Result's
# type='anthropic' versus the LLM Result's type='g4f' (see CLAUDE.md section 7, Data
# Models). The official API returns the image bytes directly as base64
# (Interaction.output_image.data), so this always takes the b64_json branch and never sets
# url -- unlike g4f image providers, which need to be saved to a local file under
# get_media_dir() and then served through /media/<filename>, this skips that whole local
# storage/cleanup complexity.
#
# Key routing rule (the core anti-abuse mechanism, fully mirroring call_claude_model): when
# user_api_key is non-empty, it is used preferentially to construct the client, consuming
# none of the developer account's quota; the caller (the /api/gemini-image route) uses this
# to decide whether it needs to check/increment the free quota counter -- this function
# itself has no knowledge of, and no concern for, the counter.
#
# Error classification: the 429/403 branches this relies on **have been verified against a
# real account** (2026-07-05, a real but zero-quota GEMINI_API_KEY, run directly through
# call_gemini_image_model() -> all three Nano Banana models). Real testing confirmed: (1) all
# three model IDs (gemini-3.1-flash-image/gemini-3-pro-image/gemini-3.1-flash-lite-image) are
# officially valid model names -- if an ID were misspelled, the API would return a
# "model does not exist" error like 404/400 instead of a quota error; all three requests
# hitting the same 429 error confirms all three IDs pass model validation first, so the ID
# mapping in this project's GEMINI_IMAGE_MODELS is cross-verified by this; (2) the real
# "zero quota" error shape is 429 + exception type name RateLimitError (a class in
# google-genai's internal private compatibility error layer, see below) + .status_code == 429
# (the .status attribute is absent/None, not entirely matching the "429/RESOURCE_EXHAUSTED
# status string" combination literally hinted at by the official troubleshooting docs --
# real testing shows only .status_code is a reliable signal; the .status check is a
# defensive fallback, possibly corresponding to another error path not yet observed in
# testing) + a message shaped like "Error code: 429 - {'error': {'message': 'You do not
# have enough quota to make this request.', 'code': 'too_many_requests'}}". The 403
# PERMISSION_DENIED case ("insufficient API key permissions") comes from the HTTP status
# code table published in the Gemini API's official troubleshooting docs
# (https://ai.google.dev/gemini-api/docs/troubleshooting, checked 2026-07-04), not yet
# verified against a real, permission-denied key (the key the user provided is itself a
# valid key, just zero-quota, triggering 429 rather than 403). Reading the SDK source of
# this project's pinned version (google-genai==2.10.0) directly confirms: the exception
# instance thrown by client.interactions.create() carries .status_code/.code/.status/.message
# attributes (whether it goes through the public google.genai.errors.APIError branch or the
# interactions resource's own internal compatibility error class), so these attributes are
# read via getattr() duck typing for classification, rather than importing any specific
# exception class -- these specific exception classes (like the RateLimitError hit in real
# testing) currently only live in an underscore-prefixed private submodule inside the
# google-genai package, with no stable public import path; importing them directly would be
# more fragile coupling than the CLAUDE_MODELS hardcoded mapping, and duck typing gets the
# same classification signal without depending on that private path. See the test
# test_real_world_quota_exhausted_error_maps_to_server_quota_exhausted in
# tests/test_gemini_integration.py (its default parameters are the real error shape captured
# in testing).
#
# One more behavioral difference from Claude (confirmed by real testing, see the
# google_genai.Client() call below in this function): anthropic.Anthropic()'s zero-argument
# constructor does not check for a key -- a missing key only errors when messages.create() is
# actually called; whereas google_genai.Client()'s zero-argument constructor checks the
# GOOGLE_API_KEY/GEMINI_API_KEY environment variables **immediately**, throwing a ValueError
# right at construction time if missing (not at call time). The except Exception fallback
# branch here still catches this ValueError and passes its message through to the frontend
# without causing a 500, so the user experience matches Claude's ("the feature fails if not
# configured, without affecting process startup"), just with the failure happening at a
# different specific call site.
# ==================================================
def call_gemini_image_model(prompt, model_key, user_api_key=None):
    model_id = GEMINI_IMAGE_MODELS.get(model_key)
    start_time = time.time()
    result = {
        'provider': 'Gemini',
        'success': False,
        'url': None,
        'b64_json': None,
        'error': '',
        'response_time': 0,
        'model': model_key,
        'type': 'google_genai',
    }

    if not model_id:
        result['error'] = f'Unknown Gemini model "{model_key}".'
        result['response_time'] = round(time.time() - start_time, 2)
        return result

    try:
        client = google_genai.Client(api_key=user_api_key) if user_api_key else google_genai.Client()
        interaction = client.interactions.create(model=model_id, input=prompt)

        image_content = getattr(interaction, 'output_image', None)
        image_b64 = getattr(image_content, 'data', None) if image_content else None

        if image_b64:
            result['b64_json'] = image_b64
            result['success'] = True
        else:
            result['error'] = 'Gemini did not return an image for this prompt.'

    except Exception as e:
        classification, message = _classify_google_genai_error(e)
        if classification == 'QUOTA_EXHAUSTED':
            result['error'] = 'SERVER_QUOTA_EXHAUSTED'
            result['error_code'] = 'SERVER_QUOTA_EXHAUSTED'
        elif classification == 'PERMISSION_DENIED':
            result['error'] = 'Invalid or missing Gemini API key.'
        else:
            result['error'] = message

    finally:
        result['response_time'] = round(time.time() - start_time, 2)

    return result


# ==================================================
# Official Gemini chat call (added 2026-07-06) -- the same google-genai SDK and the same key
# routing/error classification (_classify_google_genai_error()) as call_gemini_image_model(),
# but for the chat scenario: uses the Interactions API's output_text to carry the text
# result, mirroring the output_image branch above. This is the third "chat" frontier
# provider, alongside Claude/ChatGPT; the return value follows the same 7-key contract as
# Claude Result (type='google_genai_text', distinguished from the image chain's
# 'google_genai').
# ==================================================
def call_gemini_text_model(prompt, model_key, user_api_key=None, apply_persona=True):
    model_id = GEMINI_TEXT_MODELS.get(model_key)
    start_time = time.time()
    result = {
        'provider': 'Gemini',
        'success': False,
        'response': '',
        'error': '',
        'response_time': 0,
        'model': model_key,
        'type': 'google_genai_text',
    }

    if not model_id:
        result['error'] = f'Unknown Gemini model "{model_key}".'
        result['response_time'] = round(time.time() - start_time, 2)
        return result

    # apply_persona=False: see the comment above the parameter of the same name in
    # call_claude_model(), used so run_frontier_peer_review() can skip
    # FRONTIER_STYLE_PROMPTS_MAP when firing a peer review request.
    routed_prompt = prompt + FRONTIER_STYLE_PROMPTS_MAP.get(model_key, '') if apply_persona else prompt

    try:
        client = google_genai.Client(api_key=user_api_key) if user_api_key else google_genai.Client()
        interaction = client.interactions.create(model=model_id, input=routed_prompt)

        text = getattr(interaction, 'output_text', None)
        if text:
            result['response'] = detect_and_truncate(text)
            result['success'] = True
        else:
            result['error'] = 'Gemini did not return any text for this prompt.'

    except Exception as e:
        classification, message = _classify_google_genai_error(e)
        if classification == 'QUOTA_EXHAUSTED':
            result['error'] = 'SERVER_GEMINI_TEXT_QUOTA_EXHAUSTED'
            result['error_code'] = 'SERVER_GEMINI_TEXT_QUOTA_EXHAUSTED'
        elif classification == 'PERMISSION_DENIED':
            result['error'] = 'Invalid or missing Gemini API key.'
        else:
            result['error'] = message

    finally:
        result['response_time'] = round(time.time() - start_time, 2)

    return result


# ==================================================
# Official OpenAI (ChatGPT) chat call (added 2026-07-06) -- a fifth independent chain
# mirroring call_claude_model(), using the official openai SDK's
# client.chat.completions.create(). Key routing is identical to Claude: when user_api_key is
# non-empty, it is used preferentially to instantiate the client. See
# _classify_openai_error() for error classification (shared with
# call_chatgpt_image_model()).
# ==================================================
def call_chatgpt_model(prompt, model_key, user_api_key=None, apply_persona=True):
    model_id = CHATGPT_MODELS.get(model_key)
    start_time = time.time()
    result = {
        'provider': 'ChatGPT',
        'success': False,
        'response': '',
        'error': '',
        'response_time': 0,
        'model': model_key,
        'type': 'openai',
    }

    if not model_id:
        result['error'] = f'Unknown ChatGPT model "{model_key}".'
        result['response_time'] = round(time.time() - start_time, 2)
        return result

    # apply_persona=False: see the comment above the parameter of the same name in
    # call_claude_model(), used so run_frontier_peer_review() can skip
    # FRONTIER_STYLE_PROMPTS_MAP when firing a peer review request.
    routed_prompt = prompt + FRONTIER_STYLE_PROMPTS_MAP.get(model_key, '') if apply_persona else prompt

    try:
        client = openai.OpenAI(api_key=user_api_key) if user_api_key else openai.OpenAI()
        response = client.chat.completions.create(
            model=model_id,
            max_completion_tokens=CHATGPT_MAX_TOKENS,
            messages=[{"role": "user", "content": routed_prompt}],
        )

        text = response.choices[0].message.content if response.choices else ''
        result['response'] = detect_and_truncate(text or '')
        result['success'] = True

    except Exception as e:
        classification, message = _classify_openai_error(e)
        if classification == 'QUOTA_EXHAUSTED':
            result['error'] = 'SERVER_CHATGPT_QUOTA_EXHAUSTED'
            result['error_code'] = 'SERVER_CHATGPT_QUOTA_EXHAUSTED'
        elif classification == 'PERMISSION_DENIED':
            result['error'] = 'Invalid or missing ChatGPT API key.'
        else:
            result['error'] = message

    finally:
        result['response_time'] = round(time.time() - start_time, 2)

    return result


# ==================================================
# Official OpenAI image generation call (GPT Image series, added 2026-07-06) -- a sixth
# independent chain mirroring call_gemini_image_model(), using the official openai SDK's
# client.images.generate(). The return value follows the image 8-key contract, with
# b64_json carrying the result (the OpenAI image generation API returns base64 directly,
# with no need to save a local file like g4f does). See _classify_openai_error() for error
# classification (shared with call_chatgpt_model()).
# ==================================================
def call_chatgpt_image_model(prompt, model_key, user_api_key=None):
    model_id = CHATGPT_IMAGE_MODELS.get(model_key)
    start_time = time.time()
    result = {
        'provider': 'ChatGPT',
        'success': False,
        'url': None,
        'b64_json': None,
        'error': '',
        'response_time': 0,
        'model': model_key,
        'type': 'openai_image',
    }

    if not model_id:
        result['error'] = f'Unknown ChatGPT model "{model_key}".'
        result['response_time'] = round(time.time() - start_time, 2)
        return result

    try:
        client = openai.OpenAI(api_key=user_api_key) if user_api_key else openai.OpenAI()
        response = client.images.generate(model=model_id, prompt=prompt)

        image_data = response.data[0] if response and response.data else None
        b64_json = getattr(image_data, 'b64_json', None) if image_data else None

        if b64_json:
            result['b64_json'] = b64_json
            result['success'] = True
        else:
            result['error'] = 'ChatGPT did not return an image for this prompt.'

    except Exception as e:
        classification, message = _classify_openai_error(e)
        if classification == 'QUOTA_EXHAUSTED':
            result['error'] = 'SERVER_CHATGPT_IMAGE_QUOTA_EXHAUSTED'
            result['error_code'] = 'SERVER_CHATGPT_IMAGE_QUOTA_EXHAUSTED'
        elif classification == 'PERMISSION_DENIED':
            result['error'] = 'Invalid or missing ChatGPT API key.'
        else:
            result['error'] = message

    finally:
        result['response_time'] = round(time.time() - start_time, 2)

    return result


# ==================================================
# Helper function: validate login state for chat history routes.
# Not logged in (including guests) is always rejected, returning (None, a 401 response);
# logged in returns (user_id, None).
# ==================================================
def _get_authenticated_user_id():
    user_id = session.get('user_id')
    if not user_id:
        return None, (jsonify({'error': 'Authentication required'}), 401)
    return user_id, None


# ==================================================
# Quota refund ledger for the "Stop Generating" button (added 2026-07-05)
#
# This project's Flask deployment is synchronous: the client aborting a fetch only
# disconnects its own end of the connection; it does not interrupt the
# anthropic.messages.create()/genai interactions.create() call still blocking on the server
# -- the free quota counter can still increment after the client has already given up
# waiting. So "clicking Stop immediately refunds the quota" cannot rely on a frontend local
# guess; it relies on this ledger instead: every time claude_chat()/gemini_image_chat()
# actually succeeds in incrementing the free quota, it records that call's request_id (a
# one-time UUID generated by the frontend) together with user_id/provider into the ledger;
# after the frontend aborts, it calls /api/claude-chat/refund or /api/gemini-image/refund
# with that same request_id, and only refunds 1 unit on a ledger hit, immediately removing
# the entry from the ledger afterward -- there is no abuse surface where a authenticated user
# repeatedly calling the refund endpoint could farm unlimited quota back, because a refund
# can only reconcile one increment that genuinely happened, never create one.
#
# Kept only in single-process memory, not persisted, not shared across instances -- the same
# spirit as the "local disk is independent per instance on GAE with multiple instances"
# simplification already accepted in this project: the worst case is the request happening
# to land on a different instance, the ledger missing, the refund failing, and the user
# losing that one quota unit -- a known edge case, not something this change set out to
# solve.
# ==================================================
_PENDING_FRONTIER_REFUNDS = {}
_PENDING_FRONTIER_REFUND_TTL_SECONDS = 600
# Guards every read-modify-write on _PENDING_FRONTIER_REFUNDS. The consume path is a
# check-then-pop compound operation; without a mutex its exactly-once behavior would rest on
# the GIL happening to not switch threads between the two dict operations, which is an
# accident of timing, not a guarantee. With the lock, two racing refund requests for the same
# request_id are serialized and exactly one can ever win (see
# tests/test_refund_ledger_concurrency.py, which races 25 threads per trial over 200 trials).
_PENDING_FRONTIER_REFUNDS_LOCK = threading.Lock()


def _record_pending_frontier_refund(request_id, user_id, provider):
    if not request_id:
        return
    now = time.time()
    with _PENDING_FRONTIER_REFUNDS_LOCK:
        stale_ids = [
            rid for rid, entry in _PENDING_FRONTIER_REFUNDS.items()
            if now - entry['recorded_at'] > _PENDING_FRONTIER_REFUND_TTL_SECONDS
        ]
        for rid in stale_ids:
            _PENDING_FRONTIER_REFUNDS.pop(rid, None)
        _PENDING_FRONTIER_REFUNDS[request_id] = {
            'user_id': user_id,
            'provider': provider,
            'recorded_at': now,
        }


def _consume_pending_frontier_refund(request_id, user_id, provider):
    if not request_id:
        return False
    with _PENDING_FRONTIER_REFUNDS_LOCK:
        entry = _PENDING_FRONTIER_REFUNDS.get(request_id)
        if not entry or entry['user_id'] != user_id or entry['provider'] != provider:
            return False
        _PENDING_FRONTIER_REFUNDS.pop(request_id, None)
        return True


# ==================================================
# History-save cancellation registry for "Stop Generating" (added 2026-07-06)
#
# Rooted in the same cause as the quota refund ledger above: abort() only cuts the client's
# own end; compare_providers()/generate_images() saving to the database, and
# claude_chat()/gemini_image_chat() appending to history, can all keep running to completion
# on the server. This table turns the extra request_id the frontend carries when clicking
# Stop into an in-memory marker: the g4f stage checks it before calling
# save_chat_history()/save_image_history(), and Claude/Gemini check it before appending; a
# hit skips the whole write, so a generation the user already clicked Stop on doesn't later
# surface as a record in Recents they think doesn't exist. Claude/Gemini reuse their existing
# refund request_id, marking it as a side effect inside the /refund endpoint, with no need
# for the frontend to fire an extra request for this; the g4f stage's request_id is
# independent, marked by two new endpoints, /api/compare/cancel and
# /api/generate-images/cancel.
#
# The same simplification in spirit as the refund ledger: kept only in single-process
# memory, not persisted, not shared across instances; the request happening to land on a
# different instance is a known edge case, not something this change set out to solve.
# ==================================================
_CANCELLED_HISTORY_REQUESTS = {}
_CANCELLED_HISTORY_REQUEST_TTL_SECONDS = 600


def _mark_request_cancelled(request_id):
    if not request_id:
        return
    now = time.time()
    stale_ids = [
        rid for rid, ts in _CANCELLED_HISTORY_REQUESTS.items()
        if now - ts > _CANCELLED_HISTORY_REQUEST_TTL_SECONDS
    ]
    for rid in stale_ids:
        _CANCELLED_HISTORY_REQUESTS.pop(rid, None)
    _CANCELLED_HISTORY_REQUESTS[request_id] = now


def _is_request_cancelled(request_id):
    if not request_id:
        return False
    return request_id in _CANCELLED_HISTORY_REQUESTS


# ==================================================
# Helper function: append the Claude/Gemini result into an already-existing history record
# (added 2026-07-05)
#
# Background (the bug this fixes): /api/compare and /api/generate-images each call
# save_chat_history()/save_image_history() immediately after getting the g4f results, saving
# to the database and returning history_id; only at that point does the frontend start
# firing the extra POST /api/claude-chat / POST /api/gemini-image request. So the
# Claude/Gemini result is always computed after that history record has already been saved.
# The old implementation only appended it into the browser's in-memory data.results array
# for that page render, never writing it back to Firestore, so when the user reopened
# /history/<id> or /image-history/<id>, the Claude/Gemini result card they had just seen and
# could even download would vanish without a trace. Fix: claude_chat()/gemini_image_chat()
# now both accept an optional request body field history_id (the frontend forwards the
# history_id returned by /api/compare or /api/generate-images as-is), and append this call's
# result to that history record after either success or failure.
#
# Key design decisions:
# 1. What gets appended is the result dict **the backend just computed itself**, not
#    arbitrary JSON submitted by the client -- history_id is only a locator for "which
#    record to write to"; the result content itself is entirely determined by the server,
#    avoiding the attack surface of trusting client-constructed result data.
# 2. This is not "mixing Claude/Gemini results into save_chat_history()/save_image_history()"
#    (an explicitly forbidden approach historically -- that would mean letting Claude/Gemini
#    take part in creating new history records): these two append_* functions can only
#    append to a record that **already exists**; the only entry point that creates a new
#    record is still save_chat_history()/save_image_history(), still only triggered by the
#    g4f call chain.
# 3. When history_id is missing (e.g. a guest -- Claude/Gemini are already fully locked out
#    for guests, so this path is never reached; or the g4f side's own persistence failed so
#    there is no history_id), both functions simply skip with no error -- consistent with the
#    existing principle that "a persistence failure does not affect the main result being
#    returned"; a missing history_id should not make this Claude/Gemini request itself fail.
# 4. append_chat_history_result()/append_image_history_result() already do ownership
#    validation internally; this layer only needs to handle two failure cases: "not
#    found/not owned by this user/Firebase unavailable" (returns False) and a raised
#    exception, both of which are only logged, never propagated to the caller.
# ==================================================
def _append_claude_result_to_history(user_id, history_id, result):
    if not history_id:
        return
    try:
        appended = append_chat_history_result(user_id, history_id, result)
        if not appended:
            logger.warning(
                f"Could not append Claude result to chat history {history_id} for user {user_id} "
                "(entry not found, not owned by this user, or Firebase unavailable)"
            )
    except Exception as e:
        logger.error(
            f"Failed to append Claude result to chat history {history_id}: {e}",
            exc_info=True
        )


# ==================================================
# Local-disk conversion for Gemini/ChatGPT image results before saving to history
# (added 2026-07-06)
#
# When b64_json is embedded directly into the Firestore 'results' array, once the base64
# length crosses roughly 1MB (verified against the real project, see
# tests/test_image_history_media_cleanup_whitebox.py), Firestore's hard limit on "the size of
# a nested entity's property inside an array" causes the write to fail with 400 Property
# array contains an invalid nested entity -- the gpt-image family's default output almost
# always crosses this threshold. The fix is to decode the base64 to a local file under
# get_media_dir() (the same directory/route as g4f images) before persisting, writing only
# the url to Firestore and clearing b64_json. The result object returned to the frontend for
# this request is unaffected, still rendering immediately with the full b64_json, with no
# need for an extra /media round trip.
#
# On a decode/write failure, the result **must not** be returned unchanged: that would hand
# the still-huge b64_json as-is to append_image_history_result() to write to Firestore,
# hitting the same 1MB limit and raising an exception, which the caller
# (_append_frontier_image_result()/_append_gemini_result_to_image_history()) would only log
# and swallow -- so the whole result would never make it into the results array, and the
# user would see the generation succeed in the frontend, only to find the entire record gone
# when they later check history (hit on a real GAE deploy on 2026-07-08; the exact cause of
# the write failure is uncertain, possibly a single instance's local disk being full, but
# regardless of the cause, a failed local write must not go on to blow up the Firestore
# write). On a write failure, return a small failure result with no b64_json instead,
# guaranteeing this record always gets appended to history, even if it only truthfully
# records "the image was generated successfully but could not be saved."
# ==================================================
def _persist_image_result_local_copy(result):
    b64_json = result.get('b64_json')
    if not b64_json:
        return result
    try:
        image_bytes = base64.b64decode(b64_json)
        media_dir = get_media_dir()
        os.makedirs(media_dir, exist_ok=True)
        filename = f"{secrets.token_hex(16)}.png"
        with open(os.path.join(media_dir, filename), 'wb') as f:
            f.write(image_bytes)
        persisted = dict(result)
        persisted['url'] = f"/media/{filename}"
        persisted['b64_json'] = None
        return persisted
    except Exception as e:
        logger.error(f"Failed to persist local copy of {result.get('provider')} image: {e}", exc_info=True)
        failed = dict(result)
        failed['success'] = False
        failed['url'] = None
        failed['b64_json'] = None
        failed['error'] = 'The image was generated but could not be saved to history (local storage error).'
        return failed


def _append_gemini_result_to_image_history(user_id, history_id, result):
    if not history_id:
        return
    try:
        result = _persist_image_result_local_copy(result)
        appended = append_image_history_result(user_id, history_id, result)
        if not appended:
            logger.warning(
                f"Could not append Gemini result to image history {history_id} for user {user_id} "
                "(entry not found, not owned by this user, or Firebase unavailable)"
            )
    except Exception as e:
        logger.error(
            f"Failed to append Gemini result to image history {history_id}: {e}",
            exc_info=True
        )


# ==================================================
# Generic version (added 2026-07-06), shared by the three newly added frontier providers
# ChatGPT text, Gemini text, and ChatGPT image -- the dedicated wrapper functions for
# Claude/Gemini image above are legacy holdovers with fully identical behavior; this just
# turns provider_label from a hardcoded value into a parameter, avoiding three near-identical
# copies of the same code.
# ==================================================
def _append_frontier_chat_result(user_id, history_id, result, provider_label):
    if not history_id:
        return
    try:
        appended = append_chat_history_result(user_id, history_id, result)
        if not appended:
            logger.warning(
                f"Could not append {provider_label} result to chat history {history_id} for user {user_id} "
                "(entry not found, not owned by this user, or Firebase unavailable)"
            )
    except Exception as e:
        logger.error(
            f"Failed to append {provider_label} result to chat history {history_id}: {e}",
            exc_info=True
        )


def _append_frontier_image_result(user_id, history_id, result, provider_label):
    if not history_id:
        return
    try:
        result = _persist_image_result_local_copy(result)
        appended = append_image_history_result(user_id, history_id, result)
        if not appended:
            logger.warning(
                f"Could not append {provider_label} result to image history {history_id} for user {user_id} "
                "(entry not found, not owned by this user, or Firebase unavailable)"
            )
    except Exception as e:
        logger.error(
            f"Failed to append {provider_label} result to image history {history_id}: {e}",
            exc_info=True
        )


# ==================================================
# Helper function: prepare the context needed for the Trial Quota badge in index()'s
# template render (added 2026-07-05)
#
# Only queries the real Firestore count when logged in -- guests/anonymous users are fully
# locked out of Claude/Gemini (see CLAUDE.md section 6), so there is no quota to show, and
# both quota values are None, with the template not rendering the badge based on that
# (consistent with the "guest/anonymous = fully unavailable" semantics already used by the
# Claude/Gemini provider cards' own login-state lock check, not a "degrade to showing 0/10"
# semantics). The two constants CLAUDE_FREE_TIER_LIMIT/GEMINI_FREE_TIER_LIMIT are injected
# regardless of login state, so the frontend's "quota exhausted" dialog can dynamically
# compose the correct count text, avoiding hardcoding this number in JS and forgetting to
# sync it if the limit changes in the future.
# ==================================================
def _get_frontier_quota_context():
    claude_quota = None
    gemini_quota = None
    chatgpt_quota = None
    gemini_text_quota = None
    chatgpt_image_quota = None
    if session.get('user_id'):
        user_id = session['user_id']
        claude_quota = {'used': get_claude_free_tier_usage(user_id), 'limit': CLAUDE_FREE_TIER_LIMIT}
        gemini_quota = {'used': get_gemini_free_tier_usage(user_id), 'limit': GEMINI_FREE_TIER_LIMIT}
        chatgpt_quota = {'used': get_free_tier_usage(user_id, CHATGPT_FREE_TIER_FIELD), 'limit': CHATGPT_FREE_TIER_LIMIT}
        gemini_text_quota = {'used': get_free_tier_usage(user_id, GEMINI_TEXT_FREE_TIER_FIELD), 'limit': GEMINI_TEXT_FREE_TIER_LIMIT}
        chatgpt_image_quota = {'used': get_free_tier_usage(user_id, CHATGPT_IMAGE_FREE_TIER_FIELD), 'limit': CHATGPT_IMAGE_FREE_TIER_LIMIT}
    return {
        'claude_quota': claude_quota,
        'gemini_quota': gemini_quota,
        'chatgpt_quota': chatgpt_quota,
        'gemini_text_quota': gemini_text_quota,
        'chatgpt_image_quota': chatgpt_image_quota,
        'claude_free_tier_limit': CLAUDE_FREE_TIER_LIMIT,
        'gemini_free_tier_limit': GEMINI_FREE_TIER_LIMIT,
        'chatgpt_free_tier_limit': CHATGPT_FREE_TIER_LIMIT,
        'gemini_text_free_tier_limit': GEMINI_TEXT_FREE_TIER_LIMIT,
        'chatgpt_image_free_tier_limit': CHATGPT_IMAGE_FREE_TIER_LIMIT,
    }


# ==================================================
# Home page (passes a rigorous, correlated data mapping through Jinja2)
# ==================================================
@app.route('/')
def index():
    if not session.get('user_id') and not session.get('is_guest'):
        return render_template('home.html')

    quota_context = _get_frontier_quota_context()

    if not G4F_AVAILABLE:
        return render_template(
            'index.html',
            providers=[],
            provider_models_json={},
            image_providers=[],
            image_provider_models_json={},
            **quota_context
        )

    provider_list = []
    provider_models_json = {}

    # Organize provider data, building an exact name-to-model mapping dict
    for p in G4F_PROVIDERS:
        name = p.__name__
        models = PROVIDER_MODELS_MAP.get(name, [])

        if models:
            provider_list.append({
                'name': name,
                'default_model': models[0]
            })
            # Store in the dict so the frontend can convert it into a JavaScript object for dynamic single-select filtering
            provider_models_json[name] = models

    # Same as above, organize text-to-image provider data (an independent mapping table, fully separate from text providers)
    image_provider_list = []
    image_provider_models_json = {}
    for p in IMAGE_PROVIDERS:
        name = p.__name__
        models = IMAGE_PROVIDER_MODELS_MAP.get(name, [])

        if models:
            image_provider_list.append({
                'name': name,
                'default_model': models[0]
            })
            image_provider_models_json[name] = models

    # Inject the structured data into the frontend
    return render_template(
        'index.html',
        providers=provider_list,
        provider_models_json=provider_models_json,
        image_providers=image_provider_list,
        image_provider_models_json=image_provider_models_json,
        **quota_context
    )


@app.route('/home')
def home():
    session.pop('is_guest', None)
    return redirect(url_for('index'))


# ==================================================
# Read-only history detail page: shows a given history record's original prompt + full
# result snapshot.
# GET /history/<history_id>
#
# Logged-in user: fetch the record from Firestore with an id + ownership check, and inject
# it directly into the template render.
# Guest: chat history is never saved to the database, so there is nothing to look up here --
# render an empty shell template, and let client-side JS look it up and render it itself from
# sessionStorage (the guestHistory persisted copy maintained in history.html), keyed by the
# history_id in the URL; the server never touches guest data at any point.
# Anonymous/unauthenticated: consistent with index()'s identity routing, hand back to
# index() to handle (rendering home.html).
# ==================================================
@app.route('/history/<history_id>')
def view_history(history_id):
    if not session.get('user_id') and not session.get('is_guest'):
        return redirect(url_for('index'))

    if session.get('user_id'):
        entry = get_chat_history_by_id(session['user_id'], history_id)
        if not entry:
            flash('History entry not found.', 'error')
            return redirect(url_for('index'))
        return render_template(
            'history.html',
            history_id=history_id,
            history_entry=entry,
            is_guest_view=False
        )

    return render_template(
        'history.html',
        history_id=history_id,
        history_entry=None,
        is_guest_view=True
    )


# ==================================================
# Read-only text-to-image history detail page: shows a given image generation record's
# original prompt + full result snapshot.
# GET /image-history/<history_id>
#
# Key difference from view_history(): the image version of the Recents sidebar is
# specifically restricted to logged-in users only (see CLAUDE.md section 6, "Text-to-image
# Recents access restriction"); guests and anonymous users are always redirected back to
# index(), unlike chat history which renders an empty shell for guests to be filled in
# client-side from sessionStorage -- image generation results are never saved to the
# database for guests, and no client-side temporary record is offered either, so there is
# nothing to show a guest visiting this URL; a direct redirect is clearer than rendering a
# shell that is guaranteed to be empty.
# ==================================================
@app.route('/image-history/<history_id>')
def view_image_history(history_id):
    if not session.get('user_id'):
        return redirect(url_for('index'))

    entry = get_image_history_by_id(session['user_id'], history_id)
    if not entry:
        flash('Image history entry not found.', 'error')
        return redirect(url_for('index'))

    return render_template(
        'image_history.html',
        history_id=history_id,
        history_entry=entry
    )


# ==================================================
# Personal API key config page (added, see section 3)
# GET /apikey-config
#
# Purely a static page + client-side localStorage binding, no login guard needed -- it
# doesn't itself fire any request that needs permission; it only stores the Claude API key
# locally in the browser. The real permission/quota validation happens in the
# /api/claude-chat route. The ChatGPT/Gemini input fields are currently just placeholders,
# with no storage wired up.
# ==================================================
@app.route('/apikey-config')
def apikey_config():
    return render_template('apikey-config.html')


# ==================================================
# Get all available providers and their supported model lists
# GET /api/providers
# ==================================================
@app.route('/api/providers', methods=['GET'])
def get_providers():
    if not G4F_AVAILABLE:
        return jsonify([])

    provider_list = []

    for p in G4F_PROVIDERS:
        name = p.__name__
        models = PROVIDER_MODELS_MAP.get(name, ['unknown'])
        provider_list.append({
            'name': name,
            'models': models,            
            'default_model': models[0],   
            'type': 'g4f',
            'status': 'available'
        })

    return jsonify(provider_list)


# ==================================================
# Get all available text-to-image providers and their supported model lists
# GET /api/image-providers
# ==================================================
@app.route('/api/image-providers', methods=['GET'])
def get_image_providers():
    if not G4F_AVAILABLE:
        return jsonify([])

    provider_list = []

    for p in IMAGE_PROVIDERS:
        name = p.__name__
        models = IMAGE_PROVIDER_MODELS_MAP.get(name, ['unknown'])
        provider_list.append({
            'name': name,
            'models': models,
            'default_model': models[0],
            'type': 'g4f_image',
            'status': 'available'
        })

    return jsonify(provider_list)


# ==================================================
# Core endpoint: compare multiple providers at the same time
# POST /api/compare
# ==================================================
@app.route('/api/compare', methods=['POST'])
def compare_providers():
    try:
        data = request.get_json()

        if not data or 'prompt' not in data:
            return jsonify({
                'error': 'Prompt is required'
            }), 400

        if not G4F_AVAILABLE:
            return jsonify({
                'error': 'g4f is not available'
            }), 503

        prompt = data['prompt']

        # List of provider names the user selected
        selected_providers = data.get('providers', [])

        # Frontier-only mode (added 2026-07-08): once the frontend one-click-locks the free
        # g4f providers, this separate boolean field explicitly tells the backend "test zero
        # free providers this time" -- this cannot be expressed by an empty providers array,
        # since an empty array has historically always been reused for the default "test
        # everything" semantics (see the else branch below); the two kinds of "empty" must be
        # distinguished by different fields.
        frontier_only = bool(data.get('frontier_only'))

        # A single model name globally specified by the user (optional, for backward
        # compatibility with old callers; overridden per-provider when provider_models is
        # present)
        requested_model = data.get('model', None)

        # The model independently selected for each provider (optional, added 2026-07-09, see
        # providerModelSelections in the frontend's index.html): {provider_name: model_name}.
        # Any provider not present in this dict falls back to requested_model.
        provider_models = data.get('provider_models') or {}

        def _requested_model_for(name):
            return provider_models.get(name, requested_model)

        # A one-time UUID the frontend generates for this call, used for "Stop Generating"
        # save cancellation (see the comment above _is_request_cancelled()); this is a
        # different namespace from Claude/Gemini's own request_id, so there is no conflict.
        request_id = data.get('request_id')

        # Max thread count
        max_workers = min(
            data.get('max_workers', 3),
            5
        )

        logger.info(
            f"Comparing providers for prompt: {prompt[:50]}..."
        )

        # Filter down to the provider instances that need testing
        if frontier_only:
            providers_to_test = []
        elif selected_providers:
            providers_to_test = [
                p for p in G4F_PROVIDERS
                if p.__name__ in selected_providers
            ]
            if not providers_to_test:
                return jsonify({
                    'error': 'No valid providers found'
                }), 400
        else:
            providers_to_test = G4F_PROVIDERS

        results = []

        # In the frontier_only scenario, providers_to_test is deliberately empty -- skip the
        # entire g4f concurrent stage and go straight to sorting/saving, giving the frontend
        # an empty-results history_id for later Claude/ChatGPT/Gemini calls to append to.
        # ThreadPoolExecutor(max_workers=0) raises ValueError, so this whole block must be
        # skipped.
        if providers_to_test:
            with ThreadPoolExecutor(
                max_workers=min(max_workers, len(providers_to_test))
            ) as executor:

                futures = {
                    executor.submit(
                        test_g4f_provider,
                        p,
                        prompt,
                        _requested_model_for(p.__name__)
                    ): p
                    for p in providers_to_test
                }

                for future, provider in futures.items():
                    try:
                        result = future.result(timeout=21)
                        results.append(result)
                        logger.info(
                            f"Completed: {result['provider']} "
                            f"success={result['success']}"
                        )
                    except Exception as e:
                        name = provider.__name__
                        fallback_model = determine_actual_model(name, _requested_model_for(name))
                        fallback_result = init_result_object(name, fallback_model)
                        if isinstance(e, TimeoutError):
                            fallback_result['error'] = 'The system is busy and trying to reconnect. Please try again shortly.'
                            logger.warning(f"Provider {name} timed out after 21s")
                        else:
                            fallback_result['error'] = f'Execution error: {str(e)}'
                            logger.error(f"Error testing {name}: {e}", exc_info=True)
                        results.append(fallback_result)

        # Initialize an empty peer_reviews list for every result, keeping the 8-field
        # contract's field present. Peer review itself no longer runs here -- it is now
        # deferred until the frontend has this round's full result set (g4f + possibly
        # frontier models), then uniformly calling POST /api/peer-review to trigger cross
        # g4f/frontier-model peer review (see the comment above run_cross_peer_review(), and
        # the CLAUDE.md update log). This is designed this way because peer review needs to
        # see both the g4f and frontier model results at the same time for two-way review,
        # and the frontier model calls happen after /api/compare returns.
        for r in results:
            r['peer_reviews'] = []

        # Sort: success first, then shorter response time first
        results.sort(
            key=lambda x: (
                not x['success'],
                x['response_time']
            )
        )

        successful_count = sum(1 for r in results if r['success'])

        # Logged-in users get their chat history persisted; guests are not saved. A
        # persistence failure does not affect the comparison results returned for this
        # request. When the user clicked Stop and this request_id has already been marked
        # cancelled, skip saving entirely -- so no history record is created that the user
        # thinks doesn't exist (see the comment above _is_request_cancelled()).
        history_id = None
        if session.get('user_id') and not _is_request_cancelled(request_id):
            try:
                saved = save_chat_history(session['user_id'], prompt, results)
                if saved:
                    history_id = saved['id']
            except Exception as e:
                logger.error(f"Failed to save chat history: {e}", exc_info=True)

        return jsonify({
            'prompt': prompt,
            'total_providers': len(results),
            'successful_providers': successful_count,
            'results': results,
            'history_id': history_id
        })

    except Exception as e:
        logger.error(f"Error in compare_providers: {str(e)}", exc_info=True)
        return jsonify({
            'error': 'Service temporarily unavailable. Please try again later.'
        }), 500


# ==================================================
# POST /api/compare/cancel (added 2026-07-06, the companion endpoint for "Stop Generating")
#
# When the frontend clicks Stop, besides abort()-ing the /api/compare fetch, it also fires a
# call to this endpoint carrying that same call's request_id, recording it into
# _CANCELLED_HISTORY_REQUESTS (see the comment above it). No login guard is needed -- guests
# and anonymous users can also use the g4f comparison feature, and this endpoint itself
# neither reads nor writes any owned data; marking a request_id that doesn't exist or has
# already been used is a side-effect-free no-op.
# ==================================================
@app.route('/api/compare/cancel', methods=['POST'])
def compare_cancel():
    data = request.get_json() or {}
    _mark_request_cancelled(data.get('request_id'))
    return jsonify({'ok': True})


# The max number of pending peer-review result entries accepted per request -- prevents a
# client from submitting an arbitrary number of client-assembled "success": true results
# (once a frontier model entry is accepted as a reviewer, it genuinely spends the
# developer's/user's official API quota); this pins the scale of peer review calls per
# request to the same order of magnitude as "how many real providers this project has in
# total" (currently 4 g4f + 3 frontier text = 7), so it does not grow linearly with request
# body size.
MAX_PEER_REVIEW_ENTRIES = 10


def _valid_g4f_entry(item):
    provider = item.get('provider')
    model = item.get('model')
    return provider in PROVIDER_MODELS_MAP and model in PROVIDER_MODELS_MAP.get(provider, [])


# Validation rules for type field -> (kind, availability flag, model mapping table); only
# the three frontier text providers and g4f need to be registered here; image-type/
# frontier-image types (google_genai/openai_image, etc.) are out of scope for text peer
# review, and never being listed in this table means they can never be accepted.
_FRONTIER_ENTRY_RULES = {
    'anthropic': ('Claude', lambda: CLAUDE_AVAILABLE, CLAUDE_MODELS),
    'openai': ('ChatGPT', lambda: CHATGPT_AVAILABLE, CHATGPT_MODELS),
    'google_genai_text': ('Gemini', lambda: GEMINI_AVAILABLE, GEMINI_TEXT_MODELS),
}


# Frontier kind -> the official key request header name used to initiate peer review --
# the same header used when that provider originally answered this round's prompt (see
# claude_chat()/chatgpt_chat()/gemini_text_chat()); it does not read a client-claimed key
# ownership from the request body, avoiding trusting a JSON field that could be arbitrarily
# constructed.
_FRONTIER_KEY_HEADERS = {
    'Claude': 'X-User-Claude-Key',
    'ChatGPT': 'X-User-ChatGPT-Key',
    'Gemini': 'X-User-Gemini-Key',
}


def _sanitize_peer_review_entries(raw_results):
    """Filters/validates the client-submitted results list into the entries shape
    run_cross_peer_review() needs, never trusting the client-claimed provider/model/type
    combination -- it must actually match the known mapping tables, and the corresponding
    *_AVAILABLE flag must be true, otherwise the whole entry is dropped (no error, silently
    ignored, consistent with how other routes leniently handle unrecognized input). Returns
    (entries, has_frontier_reviewer_candidate).
    """
    entries = []
    has_frontier = False

    for item in raw_results[:MAX_PEER_REVIEW_ENTRIES]:
        if not isinstance(item, dict) or not item.get('success'):
            continue
        response_text = item.get('response')
        if not response_text or not isinstance(response_text, str):
            continue

        provider = item.get('provider')
        model = item.get('model')
        item_type = item.get('type')

        if item_type == 'g4f':
            if not _valid_g4f_entry(item):
                continue
            entries.append({
                'kind': 'g4f', 'provider': provider, 'model': model,
                'response': response_text, 'user_api_key': None,
            })
            continue

        rule = _FRONTIER_ENTRY_RULES.get(item_type)
        if not rule:
            continue
        kind, is_available, models_map = rule
        if provider != kind or not is_available() or model not in models_map:
            continue
        has_frontier = True
        user_api_key = request.headers.get(_FRONTIER_KEY_HEADERS[kind], '').strip() or None
        entries.append({
            'kind': kind, 'provider': provider, 'model': model,
            'response': response_text,
            'user_api_key': user_api_key,
        })

    return entries, has_frontier


# ==================================================
# POST /api/peer-review (added 2026-07-07, replacing the peer review stage that used to be
# hardcoded inside compare_providers() and only covered the g4f namespace)
#
# Once the frontend has this round's full result set (g4f's /api/compare + the independent
# requests for whichever of Claude/ChatGPT/Gemini were checked), it sends the merged results
# array here in one shot, uniformly triggering two-way peer review across g4f/frontier models
# (see the comment above run_cross_peer_review()). Request body:
#   {"results": [...], "history_id": "..." (optional)}
# Each result follows the 7-key text contract's fields (provider/model/type/success/response);
# frontier model entries can additionally carry user_api_key (the user's own key read from
# localStorage on the frontend; if their own key was used to answer this round's prompt, the
# same key is used for review too, per the CLAUDE.md convention that "review reuses the
# answering key, consuming no extra quota").
#
# Security boundary (because reviewing frontier models is a real API call paid for by the
# developer/user, the client cannot be trusted unconditionally):
# 1. _sanitize_peer_review_entries() validates/drops any entry whose provider/model/type
#    combination doesn't match a known mapping table, or whose provider is currently
#    unavailable, and only looks at the first MAX_PEER_REVIEW_ENTRIES entries overall --
#    preventing the client from forging an arbitrary number of fake "success": true entries
#    to trigger unlimited real, paid calls.
# 2. As soon as any entry in the sanitized list is a frontier model, it must first pass the
#    _get_authenticated_user_id() auth guard (the exact same guard as /api/claude-chat and
#    other frontier routes); a purely g4f entry list needs no login, keeping today's free
#    guest/anonymous peer review experience unchanged.
# 3. Reuses the existing free-quota decision (this review does not check or increment any
#    *_free_tier_usage counter) -- per the CLAUDE.md convention that "review consumes no
#    extra quota"; cost is already bounded by the entry-count cap in point 1.
# When fewer than 2 valid entries remain, return empty results directly, without starting any
# thread pool/real calls (mirroring compare_providers()'s original trigger condition of
# "len(providers_to_test) >= 2 and len(successful_results) >= 2").
# When history_id is non-empty and the current user is logged in, write the final peer review
# results back into that history record in place (see the comment above
# update_chat_history_peer_reviews()) -- failure is only logged, not affecting this response.
# ==================================================
@app.route('/api/peer-review', methods=['POST'])
def peer_review():
    try:
        data = request.get_json() or {}
        raw_results = data.get('results')
        history_id = data.get('history_id')

        if not isinstance(raw_results, list):
            return jsonify({'error': 'results must be a list'}), 400

        entries, has_frontier_reviewer_candidate = _sanitize_peer_review_entries(raw_results)

        user_id = None
        if has_frontier_reviewer_candidate:
            user_id, err_response = _get_authenticated_user_id()
            if err_response:
                return err_response
        else:
            user_id = session.get('user_id')

        if len(entries) < 2:
            return jsonify({'peer_reviews': {}})

        # Separate try-except: a total crash of the peer review stage (e.g. an exception
        # while building tasks) should not turn this request into a 500 -- the same
        # robustness principle as the old compare_providers()'s "a crash in the peer review
        # phase doesn't affect the first round's results," just that peer review is now its
        # own request, so "fall back to returning empty peer reviews" is the equivalent form
        # here.
        try:
            peer_reviews = run_cross_peer_review(entries)
        except Exception as e:
            logger.error(f"Cross peer review phase failed entirely: {e}", exc_info=True)
            peer_reviews = {entry['provider']: [] for entry in entries}

        if user_id and history_id:
            try:
                update_chat_history_peer_reviews(user_id, history_id, peer_reviews)
            except Exception as e:
                logger.error(f"Failed to persist peer reviews to history {history_id}: {e}", exc_info=True)

        return jsonify({'peer_reviews': peer_reviews})

    except Exception as e:
        logger.error(f"Error in peer_review: {str(e)}", exc_info=True)
        return jsonify({
            'error': 'Service temporarily unavailable. Please try again later.'
        }), 500


# ==================================================
# Test a single provider
# POST /api/test-single
# ==================================================
@app.route('/api/test-single', methods=['POST'])
def test_single_provider():
    try:
        data = request.get_json()

        if not data or 'prompt' not in data or 'provider' not in data:
            return jsonify({
                'error': 'Prompt and provider are required'
            }), 400

        if not G4F_AVAILABLE:
            return jsonify({
                'error': 'g4f is not available'
            }), 503

        prompt = data['prompt']
        provider_name = data['provider']
        requested_model = data.get('model', None)

        # Look up the matching provider
        provider = next(
            (p for p in G4F_PROVIDERS if p.__name__ == provider_name),
            None
        )

        if not provider:
            return jsonify({
                'error': f'Provider "{provider_name}" not found'
            }), 404

        result = test_g4f_provider(provider, prompt, requested_model)
        return jsonify(result)

    except Exception as e:
        logger.error(f"Error in test_single_provider: {str(e)}", exc_info=True)
        return jsonify({
            'error': 'Service temporarily unavailable. Please try again later.'
        }), 500


# ==================================================
# Official Claude (Anthropic) chat endpoint
# POST /api/claude-chat
#
# A third route, fully independent from /api/compare -- it does not go through the
# ThreadPoolExecutor concurrent scheduler, does not participate in peer review, and a single
# request only calls one Claude model. Core of the permission and cost control (anti-abuse):
# 1. Guests/anonymous users always get 401 (reuses _get_authenticated_user_id, the same guard
#    as the chat/image history routes) -- the frontend grays out the Claude checkbox; this is
#    the second layer of defense on the backend side.
# 2. When a non-empty X-User-Claude-Key header is present, that key is used preferentially to
#    instantiate the client, without checking/consuming the free quota counter at all.
# 3. When no personal key is brought, checks whether this user's claude_free_tier_usage has
#    already reached CLAUDE_FREE_TIER_LIMIT (currently 10); if so, blocks the request
#    outright without calling the developer's API, returning
#    {"error": "FREE_TIER_EXHAUSTED"}; the counter is only incremented after a successful
#    call (a failed call does not consume quota).
# 4. call_claude_model() internally translates balance exhaustion (verified in testing as
#    400 + invalid_request_error + a message containing "credit balance"; billing_error is
#    only a compatibility fallback) into error_code == 'SERVER_CREDITS_EXHAUSTED'; this route
#    converts that into a unified JSON error body.
# 5. Optional request body field history_id (added 2026-07-05, see the comment above
#    _append_claude_result_to_history()): when non-empty, once this call has actually
#    happened (i.e. it got past the FREE_TIER_EXHAUSTED block), the Claude Result from this
#    call is appended to the chat history record for that history_id regardless of
#    success/failure, fixing the earlier bug where "the Claude result is visible on the page
#    but disappears when reopening /history/<id>."
# ==================================================
@app.route('/api/claude-chat', methods=['POST'])
def claude_chat():
    try:
        if not CLAUDE_AVAILABLE:
            return jsonify({
                'error': 'CLAUDE_UNAVAILABLE',
                'message': 'Claude integration is not available on this server.'
            }), 503

        user_id, err_response = _get_authenticated_user_id()
        if err_response:
            return err_response

        data = request.get_json() or {}
        prompt = data.get('prompt', '').strip()
        model_key = data.get('model')
        history_id = data.get('history_id')
        request_id = data.get('request_id')

        if not prompt or model_key not in CLAUDE_MODELS:
            return jsonify({
                'error': 'INVALID_REQUEST',
                'message': 'A non-empty prompt and a valid Claude model are required.'
            }), 400

        user_api_key = request.headers.get('X-User-Claude-Key', '').strip()
        using_own_key = bool(user_api_key)

        if not using_own_key:
            usage = get_claude_free_tier_usage(user_id)
            if usage >= CLAUDE_FREE_TIER_LIMIT:
                return jsonify({'error': 'FREE_TIER_EXHAUSTED'}), 403

        result = call_claude_model(prompt, model_key, user_api_key if using_own_key else None)

        if result.get('error_code') == 'SERVER_CREDITS_EXHAUSTED':
            # Under the using_own_key branch, "balance exhausted" refers to the user's own
            # key, unrelated to the developer account; when going through the developer
            # account path, reaching this point means the free-quota check earlier already
            # let it through (usage < CLAUDE_FREE_TIER_LIMIT), i.e. the user's trial quota
            # still has some left -- the problem is on the supply side (the developer
            # account ran out of money), not the user's quota being used up, so the message
            # points to contacting the developer, rather than repeating the "configure a
            # personal key" suggestion that only fits the trial-quota-exhausted scenario.
            if using_own_key:
                friendly_message = 'Your personal Claude API key has run out of credits. Please check your Anthropic account balance.'
            else:
                friendly_message = (
                    "Your free trial quota still has uses left, but the developer's Claude "
                    "API account has run out of credits. Please contact the developer to restore access."
                )
            # The version appended to history must match the card the user actually sees on
            # the page -- result['error'] at this point is still the internal raw marker
            # string 'SERVER_CREDITS_EXHAUSTED' (not this friendly message), and the
            # error_code field itself is not part of the Claude Result's 6-key contract;
            # neither should be stored into Firestore as-is.
            history_result = {k: v for k, v in result.items() if k != 'error_code'}
            history_result['error'] = friendly_message
            # Skip the append when request_id has been marked cancelled by
            # /api/claude-chat/refund (the user clicked Stop) -- see the comment above
            # _is_request_cancelled().
            if not _is_request_cancelled(request_id):
                _append_claude_result_to_history(user_id, history_id, history_result)
            return jsonify({
                'error': 'SERVER_CREDITS_EXHAUSTED',
                'message': friendly_message
            }), 503

        if result['success'] and not using_own_key:
            try:
                increment_claude_free_tier_usage(user_id)
                # Only recorded in the ledger after the increment genuinely succeeds, for the
                # "Stop Generating" refund endpoint to reconcile against (see the comment
                # above _record_pending_frontier_refund()).
                _record_pending_frontier_refund(request_id, user_id, 'claude')
            except Exception as e:
                logger.error(
                    f"Failed to increment claude_free_tier_usage for {user_id}: {e}",
                    exc_info=True
                )

        if not _is_request_cancelled(request_id):
            _append_claude_result_to_history(user_id, history_id, result)

        return jsonify(result)

    except Exception as e:
        logger.error(f"Error in claude_chat: {str(e)}", exc_info=True)
        return jsonify({
            'error': 'Service temporarily unavailable. Please try again later.'
        }), 500


# ==================================================
# POST /api/claude-chat/refund (added 2026-07-05, the companion endpoint for the "Stop
# Generating" button)
#
# When the user clicks Stop Generating, if this Claude call has already been fired (reached
# fetchClaudeResult()), the frontend calls this endpoint after abort, carrying that same
# request_id. It only actually refunds 1 unit when claude_chat() genuinely succeeded in
# incrementing the free quota, and the ledger's request_id has not already been reconciled --
# when the ledger doesn't hit (e.g. aborted during the quota check stage, a call using a
# personal key, or already refunded once), it is a side-effect-free no-op that returns no
# error; the frontend needs no special handling for that. See the comment above
# _consume_pending_frontier_refund(): this design leaves no room for "repeatedly calling this
# endpoint to farm quota back."
# ==================================================
@app.route('/api/claude-chat/refund', methods=['POST'])
def claude_chat_refund():
    try:
        user_id, err_response = _get_authenticated_user_id()
        if err_response:
            return err_response

        data = request.get_json() or {}
        request_id = data.get('request_id')

        refunded = False
        if _consume_pending_frontier_refund(request_id, user_id, 'claude'):
            try:
                refunded = bool(decrement_claude_free_tier_usage(user_id))
            except Exception as e:
                logger.error(
                    f"Failed to decrement claude_free_tier_usage for {user_id}: {e}",
                    exc_info=True
                )

        # Mark as cancelled regardless of whether the ledger hit: claude_chat() may still be
        # running in another thread and hasn't reached the append-to-history step yet (see
        # the comment above _is_request_cancelled()).
        _mark_request_cancelled(request_id)

        return jsonify({'refunded': refunded})

    except Exception as e:
        logger.error(f"Error in claude_chat_refund: {str(e)}", exc_info=True)
        return jsonify({'refunded': False}), 500


# ==================================================
# Official ChatGPT chat endpoint (added 2026-07-06)
# POST /api/chatgpt-chat
#
# Mirrors /api/claude-chat point for point (the same permission/quota/refund/cancellation
# registry pattern, see the comment above it), just swapped for ChatGPT's own call
# function/model mapping/quota constants/header/refund ledger provider key.
# ==================================================
@app.route('/api/chatgpt-chat', methods=['POST'])
def chatgpt_chat():
    try:
        if not CHATGPT_AVAILABLE:
            return jsonify({
                'error': 'CHATGPT_UNAVAILABLE',
                'message': 'ChatGPT integration is not available on this server.'
            }), 503

        user_id, err_response = _get_authenticated_user_id()
        if err_response:
            return err_response

        data = request.get_json() or {}
        prompt = data.get('prompt', '').strip()
        model_key = data.get('model')
        history_id = data.get('history_id')
        request_id = data.get('request_id')

        if not prompt or model_key not in CHATGPT_MODELS:
            return jsonify({
                'error': 'INVALID_REQUEST',
                'message': 'A non-empty prompt and a valid ChatGPT model are required.'
            }), 400

        user_api_key = request.headers.get('X-User-ChatGPT-Key', '').strip()
        using_own_key = bool(user_api_key)

        if not using_own_key:
            usage = get_free_tier_usage(user_id, CHATGPT_FREE_TIER_FIELD)
            if usage >= CHATGPT_FREE_TIER_LIMIT:
                return jsonify({'error': 'FREE_TIER_EXHAUSTED'}), 403

        result = call_chatgpt_model(prompt, model_key, user_api_key if using_own_key else None)

        if result.get('error_code') == 'SERVER_CHATGPT_QUOTA_EXHAUSTED':
            if using_own_key:
                friendly_message = 'Your personal ChatGPT API key has run out of quota. Please check your OpenAI account balance.'
            else:
                friendly_message = (
                    "Your free trial quota still has uses left, but the developer's ChatGPT "
                    "API account has run out of quota. Please contact the developer to restore access."
                )
            history_result = {k: v for k, v in result.items() if k != 'error_code'}
            history_result['error'] = friendly_message
            if not _is_request_cancelled(request_id):
                _append_frontier_chat_result(user_id, history_id, history_result, 'ChatGPT')
            return jsonify({
                'error': 'SERVER_CHATGPT_QUOTA_EXHAUSTED',
                'message': friendly_message
            }), 503

        if result['success'] and not using_own_key:
            try:
                increment_free_tier_usage(user_id, CHATGPT_FREE_TIER_FIELD)
                _record_pending_frontier_refund(request_id, user_id, 'chatgpt')
            except Exception as e:
                logger.error(
                    f"Failed to increment chatgpt_free_tier_usage for {user_id}: {e}",
                    exc_info=True
                )

        if not _is_request_cancelled(request_id):
            _append_frontier_chat_result(user_id, history_id, result, 'ChatGPT')

        return jsonify(result)

    except Exception as e:
        logger.error(f"Error in chatgpt_chat: {str(e)}", exc_info=True)
        return jsonify({
            'error': 'Service temporarily unavailable. Please try again later.'
        }), 500


@app.route('/api/chatgpt-chat/refund', methods=['POST'])
def chatgpt_chat_refund():
    try:
        user_id, err_response = _get_authenticated_user_id()
        if err_response:
            return err_response

        data = request.get_json() or {}
        request_id = data.get('request_id')

        refunded = False
        if _consume_pending_frontier_refund(request_id, user_id, 'chatgpt'):
            try:
                refunded = bool(decrement_free_tier_usage(user_id, CHATGPT_FREE_TIER_FIELD))
            except Exception as e:
                logger.error(
                    f"Failed to decrement chatgpt_free_tier_usage for {user_id}: {e}",
                    exc_info=True
                )

        _mark_request_cancelled(request_id)
        return jsonify({'refunded': refunded})

    except Exception as e:
        logger.error(f"Error in chatgpt_chat_refund: {str(e)}", exc_info=True)
        return jsonify({'refunded': False}), 500


# ==================================================
# Official Gemini chat endpoint (added 2026-07-06)
# POST /api/gemini-chat
#
# Mirrors /api/claude-chat point for point; the scenario is chat rather than image
# generation (that's /api/gemini-image's scenario). Shares the same X-User-Gemini-Key
# header semantics with /api/gemini-image (the same Gemini API key works for both text and
# image), but the quota counter/refund ledger provider key ('gemini_text') is fully
# independent from the image scenario's ('gemini'), sharing nothing.
# ==================================================
@app.route('/api/gemini-chat', methods=['POST'])
def gemini_text_chat():
    try:
        if not GEMINI_AVAILABLE:
            return jsonify({
                'error': 'GEMINI_UNAVAILABLE',
                'message': 'Gemini integration is not available on this server.'
            }), 503

        user_id, err_response = _get_authenticated_user_id()
        if err_response:
            return err_response

        data = request.get_json() or {}
        prompt = data.get('prompt', '').strip()
        model_key = data.get('model')
        history_id = data.get('history_id')
        request_id = data.get('request_id')

        if not prompt or model_key not in GEMINI_TEXT_MODELS:
            return jsonify({
                'error': 'INVALID_REQUEST',
                'message': 'A non-empty prompt and a valid Gemini model are required.'
            }), 400

        user_api_key = request.headers.get('X-User-Gemini-Key', '').strip()
        using_own_key = bool(user_api_key)

        if not using_own_key:
            usage = get_free_tier_usage(user_id, GEMINI_TEXT_FREE_TIER_FIELD)
            if usage >= GEMINI_TEXT_FREE_TIER_LIMIT:
                return jsonify({'error': 'FREE_TIER_EXHAUSTED'}), 403

        result = call_gemini_text_model(prompt, model_key, user_api_key if using_own_key else None)

        if result.get('error_code') == 'SERVER_GEMINI_TEXT_QUOTA_EXHAUSTED':
            if using_own_key:
                friendly_message = 'Your personal Gemini API key has run out of quota. Please check your Google AI account.'
            else:
                friendly_message = (
                    "Your free trial quota still has uses left, but the developer's Gemini "
                    "API account has run out of quota. Please contact the developer to restore access."
                )
            history_result = {k: v for k, v in result.items() if k != 'error_code'}
            history_result['error'] = friendly_message
            if not _is_request_cancelled(request_id):
                _append_frontier_chat_result(user_id, history_id, history_result, 'Gemini')
            return jsonify({
                'error': 'SERVER_GEMINI_TEXT_QUOTA_EXHAUSTED',
                'message': friendly_message
            }), 503

        if result['success'] and not using_own_key:
            try:
                increment_free_tier_usage(user_id, GEMINI_TEXT_FREE_TIER_FIELD)
                _record_pending_frontier_refund(request_id, user_id, 'gemini_text')
            except Exception as e:
                logger.error(
                    f"Failed to increment gemini_text_free_tier_usage for {user_id}: {e}",
                    exc_info=True
                )

        if not _is_request_cancelled(request_id):
            _append_frontier_chat_result(user_id, history_id, result, 'Gemini')

        return jsonify(result)

    except Exception as e:
        logger.error(f"Error in gemini_text_chat: {str(e)}", exc_info=True)
        return jsonify({
            'error': 'Service temporarily unavailable. Please try again later.'
        }), 500


@app.route('/api/gemini-chat/refund', methods=['POST'])
def gemini_text_chat_refund():
    try:
        user_id, err_response = _get_authenticated_user_id()
        if err_response:
            return err_response

        data = request.get_json() or {}
        request_id = data.get('request_id')

        refunded = False
        if _consume_pending_frontier_refund(request_id, user_id, 'gemini_text'):
            try:
                refunded = bool(decrement_free_tier_usage(user_id, GEMINI_TEXT_FREE_TIER_FIELD))
            except Exception as e:
                logger.error(
                    f"Failed to decrement gemini_text_free_tier_usage for {user_id}: {e}",
                    exc_info=True
                )

        _mark_request_cancelled(request_id)
        return jsonify({'refunded': refunded})

    except Exception as e:
        logger.error(f"Error in gemini_text_chat_refund: {str(e)}", exc_info=True)
        return jsonify({'refunded': False}), 500


# ==================================================
# Batched frontier chat endpoint (added 2026-08-28)
# POST /api/frontier-chat
#
# The server-side concurrent scheduler for stage 2 of the compare pipeline. Before this
# route, the three frontier providers were fired as three sequential browser fetches (each
# one awaited before the next started), so stage 2 had no real concurrency at all; now the
# frontend sends one request naming every checked frontier provider, and this route fans
# them out through the same ThreadPoolExecutor pattern stages 1 and 3 already use. The three
# standalone routes above (/api/claude-chat etc.) remain deployed unchanged -- they are the
# canonical single-provider path and the refund/cancellation endpoints still live there.
#
# Per-provider semantics are kept identical to the standalone routes (same auth guard, same
# personal-key header bypass, same quota gate before the SDK call, same increment + refund
# ledger recording on success, same friendly SERVER_*_EXHAUSTED messages), driven off
# _FRONTIER_CHAT_PROVIDERS below. The one deliberate difference: history appends do NOT
# happen inside the worker threads. append_chat_history_result() is a read-modify-write on
# the whole results array with no transaction, so two concurrent appends to the same
# document could lose one; the workers only call the SDK and settle quota, and the appends
# run sequentially in the request thread after the whole batch has been gathered, in the
# order the providers were requested. Cancellation (_is_request_cancelled) is checked at
# append time, exactly like the standalone routes.
#
# Config values are wrapped in lambdas so they resolve the module globals at call time --
# tests that patch main.CLAUDE_AVAILABLE / main.call_claude_model etc. see the same
# behavior through this route as through the standalone ones.
# ==================================================
FRONTIER_CHAT_TASK_TIMEOUT_SECONDS = 120

_FRONTIER_CHAT_PROVIDERS = {
    'claude': {
        'label': 'Claude',
        'available': lambda: CLAUDE_AVAILABLE,
        'unavailable_code': 'CLAUDE_UNAVAILABLE',
        'unavailable_message': 'Claude integration is not available on this server.',
        'models': lambda: CLAUDE_MODELS,
        'header': 'X-User-Claude-Key',
        'get_usage': lambda user_id: get_claude_free_tier_usage(user_id),
        'limit': lambda: CLAUDE_FREE_TIER_LIMIT,
        'increment': lambda user_id: increment_claude_free_tier_usage(user_id),
        'usage_field_name': 'claude_free_tier_usage',
        'call': lambda prompt, model_key, key: call_claude_model(prompt, model_key, key),
        'ledger_key': 'claude',
        'server_exhausted_code': 'SERVER_CREDITS_EXHAUSTED',
        'own_key_message': 'Your personal Claude API key has run out of credits. Please check your Anthropic account balance.',
        'developer_message': (
            "Your free trial quota still has uses left, but the developer's Claude "
            "API account has run out of credits. Please contact the developer to restore access."
        ),
        'append': lambda user_id, history_id, result: _append_claude_result_to_history(user_id, history_id, result),
    },
    'chatgpt': {
        'label': 'ChatGPT',
        'available': lambda: CHATGPT_AVAILABLE,
        'unavailable_code': 'CHATGPT_UNAVAILABLE',
        'unavailable_message': 'ChatGPT integration is not available on this server.',
        'models': lambda: CHATGPT_MODELS,
        'header': 'X-User-ChatGPT-Key',
        'get_usage': lambda user_id: get_free_tier_usage(user_id, CHATGPT_FREE_TIER_FIELD),
        'limit': lambda: CHATGPT_FREE_TIER_LIMIT,
        'increment': lambda user_id: increment_free_tier_usage(user_id, CHATGPT_FREE_TIER_FIELD),
        'usage_field_name': 'chatgpt_free_tier_usage',
        'call': lambda prompt, model_key, key: call_chatgpt_model(prompt, model_key, key),
        'ledger_key': 'chatgpt',
        'server_exhausted_code': 'SERVER_CHATGPT_QUOTA_EXHAUSTED',
        'own_key_message': 'Your personal ChatGPT API key has run out of quota. Please check your OpenAI account balance.',
        'developer_message': (
            "Your free trial quota still has uses left, but the developer's ChatGPT "
            "API account has run out of quota. Please contact the developer to restore access."
        ),
        'append': lambda user_id, history_id, result: _append_frontier_chat_result(user_id, history_id, result, 'ChatGPT'),
    },
    'gemini_text': {
        'label': 'Gemini',
        'available': lambda: GEMINI_AVAILABLE,
        'unavailable_code': 'GEMINI_UNAVAILABLE',
        'unavailable_message': 'Gemini integration is not available on this server.',
        'models': lambda: GEMINI_TEXT_MODELS,
        'header': 'X-User-Gemini-Key',
        'get_usage': lambda user_id: get_free_tier_usage(user_id, GEMINI_TEXT_FREE_TIER_FIELD),
        'limit': lambda: GEMINI_TEXT_FREE_TIER_LIMIT,
        'increment': lambda user_id: increment_free_tier_usage(user_id, GEMINI_TEXT_FREE_TIER_FIELD),
        'usage_field_name': 'gemini_text_free_tier_usage',
        'call': lambda prompt, model_key, key: call_gemini_text_model(prompt, model_key, key),
        'ledger_key': 'gemini_text',
        'server_exhausted_code': 'SERVER_GEMINI_TEXT_QUOTA_EXHAUSTED',
        'own_key_message': 'Your personal Gemini API key has run out of quota. Please check your Google AI account.',
        'developer_message': (
            "Your free trial quota still has uses left, but the developer's Gemini "
            "API account has run out of quota. Please contact the developer to restore access."
        ),
        'append': lambda user_id, history_id, result: _append_frontier_chat_result(user_id, history_id, result, 'Gemini'),
    },
}


def _run_frontier_chat_task(provider_key, user_id, prompt, model_key, request_id, user_api_key):
    """One frontier provider's quota gate + SDK call + quota settlement, run inside the batch
    executor. Returns {'payload': <what the client sees for this provider>,
    'history_result': <what gets appended to the history doc, or None>}. History appends are
    deliberately NOT done here -- see the route comment above."""
    cfg = _FRONTIER_CHAT_PROVIDERS[provider_key]
    using_own_key = bool(user_api_key)

    if not using_own_key:
        usage = cfg['get_usage'](user_id)
        if usage >= cfg['limit']():
            return {'payload': {'error': 'FREE_TIER_EXHAUSTED'}, 'history_result': None}

    result = cfg['call'](prompt, model_key, user_api_key if using_own_key else None)

    if result.get('error_code') == cfg['server_exhausted_code']:
        friendly_message = cfg['own_key_message'] if using_own_key else cfg['developer_message']
        history_result = {k: v for k, v in result.items() if k != 'error_code'}
        history_result['error'] = friendly_message
        return {
            'payload': {'error': cfg['server_exhausted_code'], 'message': friendly_message},
            'history_result': history_result,
        }

    if result['success'] and not using_own_key:
        try:
            cfg['increment'](user_id)
            _record_pending_frontier_refund(request_id, user_id, cfg['ledger_key'])
        except Exception as e:
            logger.error(
                f"Failed to increment {cfg['usage_field_name']} for {user_id}: {e}",
                exc_info=True
            )

    return {'payload': result, 'history_result': result}


@app.route('/api/frontier-chat', methods=['POST'])
def frontier_chat():
    try:
        user_id, err_response = _get_authenticated_user_id()
        if err_response:
            return err_response

        data = request.get_json() or {}
        prompt = data.get('prompt', '').strip()
        history_id = data.get('history_id')
        provider_entries = data.get('providers')

        if not prompt or not isinstance(provider_entries, list) or not provider_entries:
            return jsonify({
                'error': 'INVALID_REQUEST',
                'message': 'A non-empty prompt and a non-empty providers list are required.'
            }), 400

        # Validate and dedupe (first entry per provider wins). Unknown providers/models
        # reject the whole request -- same 400 contract as the standalone routes.
        tasks = {}
        for entry in provider_entries:
            if not isinstance(entry, dict):
                return jsonify({'error': 'INVALID_REQUEST', 'message': 'Each providers entry must be an object.'}), 400
            provider_key = entry.get('provider')
            cfg = _FRONTIER_CHAT_PROVIDERS.get(provider_key)
            if cfg is None:
                return jsonify({'error': 'INVALID_REQUEST', 'message': f'Unknown frontier provider "{provider_key}".'}), 400
            if provider_key in tasks:
                continue
            model_key = entry.get('model')
            if model_key not in cfg['models']():
                return jsonify({
                    'error': 'INVALID_REQUEST',
                    'message': f'A valid {cfg["label"]} model is required.'
                }), 400
            tasks[provider_key] = {
                'model': model_key,
                'request_id': entry.get('request_id'),
                'user_api_key': request.headers.get(cfg['header'], '').strip(),
            }

        results_by_provider = {}
        outcomes = {}

        # An unavailable SDK degrades only its own entry (unlike the standalone routes'
        # whole-request 503) so one missing integration never blocks the other providers
        # in the same batch.
        runnable = {}
        for provider_key, task in tasks.items():
            cfg = _FRONTIER_CHAT_PROVIDERS[provider_key]
            if not cfg['available']():
                results_by_provider[provider_key] = {
                    'error': cfg['unavailable_code'],
                    'message': cfg['unavailable_message'],
                }
            else:
                runnable[provider_key] = task

        if runnable:
            with ThreadPoolExecutor(max_workers=len(runnable)) as executor:
                futures = {
                    provider_key: executor.submit(
                        _run_frontier_chat_task,
                        provider_key, user_id, prompt,
                        task['model'], task['request_id'], task['user_api_key'],
                    )
                    for provider_key, task in runnable.items()
                }
                for provider_key, future in futures.items():
                    cfg = _FRONTIER_CHAT_PROVIDERS[provider_key]
                    try:
                        outcomes[provider_key] = future.result(timeout=FRONTIER_CHAT_TASK_TIMEOUT_SECONDS)
                    except TimeoutError:
                        logger.warning(f"Frontier chat task for {provider_key} timed out")
                        outcomes[provider_key] = {
                            'payload': {
                                'provider': cfg['label'], 'success': False, 'response': '',
                                'error': 'The system is busy and trying to reconnect. Please try again shortly.',
                                'response_time': FRONTIER_CHAT_TASK_TIMEOUT_SECONDS,
                                'model': runnable[provider_key]['model'],
                                'type': 'anthropic' if provider_key == 'claude' else ('openai' if provider_key == 'chatgpt' else 'google_genai_text'),
                            },
                            'history_result': None,
                        }
                    except Exception as e:
                        logger.error(f"Frontier chat task for {provider_key} failed: {e}", exc_info=True)
                        outcomes[provider_key] = {
                            'payload': {'error': 'Service temporarily unavailable. Please try again later.'},
                            'history_result': None,
                        }

        # Sequential appends in request order, in the request thread -- see the route
        # comment for why these must not run inside the worker threads.
        for provider_key in tasks:
            outcome = outcomes.get(provider_key)
            if outcome is None:
                continue
            history_result = outcome.get('history_result')
            if history_result is not None and not _is_request_cancelled(tasks[provider_key]['request_id']):
                _FRONTIER_CHAT_PROVIDERS[provider_key]['append'](user_id, history_id, history_result)
            results_by_provider[provider_key] = outcome['payload']

        return jsonify({'results': results_by_provider})

    except Exception as e:
        logger.error(f"Error in frontier_chat: {str(e)}", exc_info=True)
        return jsonify({
            'error': 'Service temporarily unavailable. Please try again later.'
        }), 500


# ==================================================
# Core endpoint: concurrently call multiple text-to-image providers to generate images
# POST /api/generate-images
#
# The same "concurrent scheduling + per-task fallback" skeleton as compare_providers(), but
# with only one stage (no peer review), and it calls test_g4f_image_provider()
# (images.generate()) rather than test_g4f_provider() (ChatCompletion.create()). Logged-in
# users' image results are persisted to an independent 'image_history' Firestore collection
# (save_image_history(), added 2026-07-04) -- it does not reuse the 'history' collection,
# because that document structure is designed around the text 7-key result DTO
# (+ peer_reviews); mixing in the 8-key image DTO would require introducing a discriminator
# field. Guest and anonymous users' image generation results are still never saved, and
# deliberately offer no sessionStorage-level temporary record either (unlike how text chat
# history handles guests) -- the image version of the Recents sidebar is specifically
# restricted to logged-in users only.
#
# This route **no longer** does any get_media_dir() local file cleanup (removed 2026-07-05;
# this used to be the cleanup_old_generated_media()/GENERATED_MEDIA_MAX_AGE_SECONDS
# age-based lazy cleanup mechanism, see CLAUDE.md section 9 for the full record of that
# incident) -- the image version of Recents history needs to be "viewable forever," and the
# history detail page's <img> tags reference exactly these local files; auto-deleting by age
# would turn images in history records older than 1 hour into 404s. Cleanup is deliberately
# skipped entirely, accepting the known cost of "local disk keeps growing with request
# volume" (see CLAUDE.md section 9 risks; when a real long-term fix is needed, it should
# migrate to persistent storage like Cloud Storage, rather than reintroducing age-based
# deletion).
# ==================================================
@app.route('/api/generate-images', methods=['POST'])
def generate_images():
    try:
        data = request.get_json()

        if not data or 'prompt' not in data:
            return jsonify({
                'error': 'Prompt is required'
            }), 400

        if not G4F_AVAILABLE:
            return jsonify({
                'error': 'g4f is not available'
            }), 503

        prompt = data['prompt']

        # List of image provider names the user selected
        selected_providers = data.get('providers', [])

        # Frontier-only mode (added 2026-07-08): mirrors the same-named field in
        # compare_providers(), see the comment above it -- an independent boolean field used
        # to distinguish "zero free providers" from "providers array is empty = test
        # everything," two meanings historically expressed by the same empty array.
        frontier_only = bool(data.get('frontier_only'))

        # A single model name globally specified by the user (optional, for backward
        # compatibility with old callers; overridden per-provider when provider_models is
        # present)
        requested_model = data.get('model', None)

        # The model independently selected for each image provider (optional, mirrors
        # compare_providers()'s provider_models): {provider_name: model_name}.
        provider_models = data.get('provider_models') or {}

        def _requested_model_for(name):
            return provider_models.get(name, requested_model)

        # A one-time UUID the frontend generates for this call, used for "Stop Generating"
        # save cancellation; an independent namespace from compare_providers()'s request_id.
        request_id = data.get('request_id')

        # Max thread count
        max_workers = min(
            data.get('max_workers', 3),
            5
        )

        logger.info(
            f"Generating images for prompt: {prompt[:50]}..."
        )

        # Filter down to the image provider instances that need to be called
        if frontier_only:
            providers_to_test = []
        elif selected_providers:
            providers_to_test = [
                p for p in IMAGE_PROVIDERS
                if p.__name__ in selected_providers
            ]
            if not providers_to_test:
                return jsonify({
                    'error': 'No valid image providers found'
                }), 400
        else:
            providers_to_test = IMAGE_PROVIDERS

        results = []

        # In the frontier_only scenario, deliberately skip the entire g4f image concurrent
        # stage (same as the comment above in compare_providers();
        # ThreadPoolExecutor(max_workers=0) raises ValueError, so this whole block must be
        # skipped).
        if providers_to_test:
            with ThreadPoolExecutor(
                max_workers=min(max_workers, len(providers_to_test))
            ) as executor:

                futures = {
                    executor.submit(
                        test_g4f_image_provider,
                        p,
                        prompt,
                        _requested_model_for(p.__name__)
                    ): p
                    for p in providers_to_test
                }

                for future, provider in futures.items():
                    name = provider.__name__
                    # Each provider's outer timeout is computed independently (see
                    # get_image_timeouts()), so a slow, aggregator-style provider (like
                    # AnyProvider) does not slow down the wait time for other providers in
                    # the same batch, nor does it get prematurely judged as timed out by the
                    # default budget.
                    _, outer_timeout = get_image_timeouts(name)
                    try:
                        result = future.result(timeout=outer_timeout)
                        results.append(result)
                        logger.info(
                            f"Image generation completed: {result['provider']} "
                            f"success={result['success']}"
                        )
                    except Exception as e:
                        fallback_model = determine_actual_image_model(name, _requested_model_for(name)) or 'default'
                        fallback_result = init_image_result_object(name, fallback_model)
                        if isinstance(e, TimeoutError):
                            fallback_result['error'] = 'The system is busy and trying to reconnect. Please try again shortly.'
                            logger.warning(
                                f"Image provider {name} timed out after {outer_timeout}s"
                            )
                        else:
                            fallback_result['error'] = f'Execution error: {str(e)}'
                            logger.error(f"Error generating image with {name}: {e}", exc_info=True)
                        results.append(fallback_result)

        # Sort: success first, then shorter response time first (consistent with compare_providers's sort contract)
        results.sort(
            key=lambda x: (
                not x['success'],
                x['response_time']
            )
        )

        successful_count = sum(1 for r in results if r['success'])

        # Logged-in users get their image generation history persisted (an independent
        # 'image_history' collection, see the comment at the top of auth/db.py); guests and
        # anonymous users are not saved -- the image version of the Recents sidebar is
        # specifically restricted to logged-in users only, and the guest side doesn't even
        # offer a browser-memory/sessionStorage-level temporary record (deliberately
        # different from how text chat history handles guests, see CLAUDE.md section 6). A
        # persistence failure does not affect the generation results returned for this
        # request, using a separate try/except. When the user clicked Stop and this
        # request_id has already been marked cancelled, skip saving entirely (see the
        # comment above _is_request_cancelled()).
        history_id = None
        if session.get('user_id') and not _is_request_cancelled(request_id):
            try:
                saved = save_image_history(session['user_id'], prompt, results)
                if saved:
                    history_id = saved['id']
            except Exception as e:
                logger.error(f"Failed to save image history: {e}", exc_info=True)

        return jsonify({
            'prompt': prompt,
            'total_providers': len(results),
            'successful_providers': successful_count,
            'results': results,
            'history_id': history_id
        })

    except Exception as e:
        logger.error(f"Error in generate_images: {str(e)}", exc_info=True)
        return jsonify({
            'error': 'Service temporarily unavailable. Please try again later.'
        }), 500


# ==================================================
# POST /api/generate-images/cancel (added 2026-07-06), mirrors /api/compare/cancel, see the
# comment above it.
# ==================================================
@app.route('/api/generate-images/cancel', methods=['POST'])
def generate_images_cancel():
    data = request.get_json() or {}
    _mark_request_cancelled(data.get('request_id'))
    return jsonify({'ok': True})


# ==================================================
# Official Google Gemini ("Nano Banana") text-to-image endpoint
# POST /api/gemini-image
#
# A fourth route, fully independent from /api/generate-images -- it does not go through the
# ThreadPoolExecutor concurrent scheduler, does not participate in the g4f image providers'
# retry/timeout budget logic, and a single request only calls one Gemini model. Core of the
# permission and cost control (anti-abuse) mirrors /api/claude-chat point for point:
# 1. Guests/anonymous users always get 401 (reuses _get_authenticated_user_id, the same
#    guard as Claude/chat/image history routes) -- the frontend grays out the Gemini
#    checkbox; this is the second layer of defense on the backend side.
# 2. When a non-empty X-User-Gemini-Key header is present, that key is used preferentially
#    to instantiate the client, without checking/consuming the free quota counter at all.
# 3. When no personal key is brought, checks whether this user's gemini_free_tier_usage has
#    already reached GEMINI_FREE_TIER_LIMIT (currently 10); if so, blocks the request
#    outright without calling the developer's API, returning
#    {"error": "FREE_TIER_EXHAUSTED"}; the counter is only incremented after a successful
#    call (a failed call does not consume quota).
# 4. call_gemini_image_model() internally translates quota exhaustion
#    (429/RESOURCE_EXHAUSTED) into error_code == 'SERVER_QUOTA_EXHAUSTED'; this route
#    converts that into a unified JSON error body.
# 5. Optional request body field history_id (added 2026-07-05, mirrors claude_chat()'s
#    history_id, see the comment above _append_gemini_result_to_image_history()): when
#    non-empty, once this call has actually happened (i.e. it got past the
#    FREE_TIER_EXHAUSTED block), the Gemini Image Result from this call is appended to the
#    image history record for that history_id regardless of success/failure, fixing the
#    earlier bug where "the Gemini result is visible and downloadable on the page but
#    disappears when reopening /image-history/<id>."
#
# Key difference from generate_images(): this route **never** calls save_image_history() to
# create a new image_history document -- Gemini still cannot start a new history record on
# its own the way g4f image providers do; it can only append to a record already created by
# /api/generate-images, via point 5 above.
# ==================================================
@app.route('/api/gemini-image', methods=['POST'])
def gemini_image_chat():
    try:
        if not GEMINI_AVAILABLE:
            return jsonify({
                'error': 'GEMINI_UNAVAILABLE',
                'message': 'Gemini integration is not available on this server.'
            }), 503

        user_id, err_response = _get_authenticated_user_id()
        if err_response:
            return err_response

        data = request.get_json() or {}
        prompt = data.get('prompt', '').strip()
        model_key = data.get('model')
        history_id = data.get('history_id')
        request_id = data.get('request_id')

        if not prompt or model_key not in GEMINI_IMAGE_MODELS:
            return jsonify({
                'error': 'INVALID_REQUEST',
                'message': 'A non-empty prompt and a valid Gemini model are required.'
            }), 400

        user_api_key = request.headers.get('X-User-Gemini-Key', '').strip()
        using_own_key = bool(user_api_key)

        if not using_own_key:
            usage = get_gemini_free_tier_usage(user_id)
            if usage >= GEMINI_FREE_TIER_LIMIT:
                return jsonify({'error': 'FREE_TIER_EXHAUSTED'}), 403

        result = call_gemini_image_model(prompt, model_key, user_api_key if using_own_key else None)

        if result.get('error_code') == 'SERVER_QUOTA_EXHAUSTED':
            # Same reasoning as claude_chat()'s SERVER_CREDITS_EXHAUSTED branch: under
            # using_own_key, it's the user's own key that's exhausted, unrelated to the
            # developer account; when going through the developer account path, reaching
            # this point means the free-quota check already let it through (trial quota
            # still has some left), the problem is on the supply side, so the message points
            # to contacting the developer.
            if using_own_key:
                friendly_message = 'Your personal Gemini API key has run out of quota. Please check your Google AI account.'
            else:
                friendly_message = (
                    "Your free trial quota still has uses left, but the developer's Gemini "
                    "API account has run out of quota. Please contact the developer to restore access."
                )
            # The version appended to history must match the card the user actually sees;
            # strip error_code (not part of the Gemini Image Result contract) and swap
            # error for the friendly message instead of the internal marker string.
            history_result = {k: v for k, v in result.items() if k != 'error_code'}
            history_result['error'] = friendly_message
            # Skip the append when request_id has been marked cancelled by
            # /api/gemini-image/refund (the user clicked Stop) -- see the comment above
            # _is_request_cancelled().
            if not _is_request_cancelled(request_id):
                _append_gemini_result_to_image_history(user_id, history_id, history_result)
            return jsonify({
                'error': 'SERVER_QUOTA_EXHAUSTED',
                'message': friendly_message
            }), 503

        if result['success'] and not using_own_key:
            try:
                increment_gemini_free_tier_usage(user_id)
                # Only recorded in the ledger after the increment genuinely succeeds, for the
                # "Stop Generating" refund endpoint to reconcile against (see the comment
                # above _record_pending_frontier_refund()).
                _record_pending_frontier_refund(request_id, user_id, 'gemini')
            except Exception as e:
                logger.error(
                    f"Failed to increment gemini_free_tier_usage for {user_id}: {e}",
                    exc_info=True
                )

        if not _is_request_cancelled(request_id):
            _append_gemini_result_to_image_history(user_id, history_id, result)

        return jsonify(result)

    except Exception as e:
        logger.error(f"Error in gemini_image_chat: {str(e)}", exc_info=True)
        return jsonify({
            'error': 'Service temporarily unavailable. Please try again later.'
        }), 500


# ==================================================
# POST /api/gemini-image/refund (added 2026-07-05), mirrors /api/claude-chat/refund, see the
# comment above it.
# ==================================================
@app.route('/api/gemini-image/refund', methods=['POST'])
def gemini_image_refund():
    try:
        user_id, err_response = _get_authenticated_user_id()
        if err_response:
            return err_response

        data = request.get_json() or {}
        request_id = data.get('request_id')

        refunded = False
        if _consume_pending_frontier_refund(request_id, user_id, 'gemini'):
            try:
                refunded = bool(decrement_gemini_free_tier_usage(user_id))
            except Exception as e:
                logger.error(
                    f"Failed to decrement gemini_free_tier_usage for {user_id}: {e}",
                    exc_info=True
                )

        # Mark as cancelled regardless of whether the ledger hit: gemini_image_chat() may
        # still be running in another thread and hasn't reached the append-to-history step
        # yet (see the comment above _is_request_cancelled()).
        _mark_request_cancelled(request_id)

        return jsonify({'refunded': refunded})

    except Exception as e:
        logger.error(f"Error in gemini_image_refund: {str(e)}", exc_info=True)
        return jsonify({'refunded': False}), 500


# ==================================================
# Official ChatGPT image generation endpoint (GPT Image series, added 2026-07-06)
# POST /api/chatgpt-image
#
# Mirrors /api/gemini-image point for point, just swapped for ChatGPT's own call
# function/model mapping/quota constants/refund ledger provider key. Shares the same
# X-User-ChatGPT-Key header semantics with /api/chatgpt-chat (the same OpenAI key works for
# both text and image), but the quota counter/refund ledger provider key ('chatgpt_image')
# is fully independent from the text scenario's ('chatgpt'). Likewise, this route **never**
# calls save_image_history() to create a new record; it can only append to a record already
# created by /api/generate-images (the same constraint as gemini_image_chat()).
# ==================================================
@app.route('/api/chatgpt-image', methods=['POST'])
def chatgpt_image_chat():
    try:
        if not CHATGPT_AVAILABLE:
            return jsonify({
                'error': 'CHATGPT_UNAVAILABLE',
                'message': 'ChatGPT integration is not available on this server.'
            }), 503

        user_id, err_response = _get_authenticated_user_id()
        if err_response:
            return err_response

        data = request.get_json() or {}
        prompt = data.get('prompt', '').strip()
        model_key = data.get('model')
        history_id = data.get('history_id')
        request_id = data.get('request_id')

        if not prompt or model_key not in CHATGPT_IMAGE_MODELS:
            return jsonify({
                'error': 'INVALID_REQUEST',
                'message': 'A non-empty prompt and a valid ChatGPT model are required.'
            }), 400

        user_api_key = request.headers.get('X-User-ChatGPT-Key', '').strip()
        using_own_key = bool(user_api_key)

        if not using_own_key:
            usage = get_free_tier_usage(user_id, CHATGPT_IMAGE_FREE_TIER_FIELD)
            if usage >= CHATGPT_IMAGE_FREE_TIER_LIMIT:
                return jsonify({'error': 'FREE_TIER_EXHAUSTED'}), 403

        result = call_chatgpt_image_model(prompt, model_key, user_api_key if using_own_key else None)

        if result.get('error_code') == 'SERVER_CHATGPT_IMAGE_QUOTA_EXHAUSTED':
            if using_own_key:
                friendly_message = 'Your personal ChatGPT API key has run out of quota. Please check your OpenAI account balance.'
            else:
                friendly_message = (
                    "Your free trial quota still has uses left, but the developer's ChatGPT "
                    "API account has run out of quota. Please contact the developer to restore access."
                )
            history_result = {k: v for k, v in result.items() if k != 'error_code'}
            history_result['error'] = friendly_message
            if not _is_request_cancelled(request_id):
                _append_frontier_image_result(user_id, history_id, history_result, 'ChatGPT')
            return jsonify({
                'error': 'SERVER_CHATGPT_IMAGE_QUOTA_EXHAUSTED',
                'message': friendly_message
            }), 503

        if result['success'] and not using_own_key:
            try:
                increment_free_tier_usage(user_id, CHATGPT_IMAGE_FREE_TIER_FIELD)
                _record_pending_frontier_refund(request_id, user_id, 'chatgpt_image')
            except Exception as e:
                logger.error(
                    f"Failed to increment chatgpt_image_free_tier_usage for {user_id}: {e}",
                    exc_info=True
                )

        if not _is_request_cancelled(request_id):
            _append_frontier_image_result(user_id, history_id, result, 'ChatGPT')

        return jsonify(result)

    except Exception as e:
        logger.error(f"Error in chatgpt_image_chat: {str(e)}", exc_info=True)
        return jsonify({
            'error': 'Service temporarily unavailable. Please try again later.'
        }), 500


@app.route('/api/chatgpt-image/refund', methods=['POST'])
def chatgpt_image_refund():
    try:
        user_id, err_response = _get_authenticated_user_id()
        if err_response:
            return err_response

        data = request.get_json() or {}
        request_id = data.get('request_id')

        refunded = False
        if _consume_pending_frontier_refund(request_id, user_id, 'chatgpt_image'):
            try:
                refunded = bool(decrement_free_tier_usage(user_id, CHATGPT_IMAGE_FREE_TIER_FIELD))
            except Exception as e:
                logger.error(
                    f"Failed to decrement chatgpt_image_free_tier_usage for {user_id}: {e}",
                    exc_info=True
                )

        _mark_request_cancelled(request_id)
        return jsonify({'refunded': refunded})

    except Exception as e:
        logger.error(f"Error in chatgpt_image_refund: {str(e)}", exc_info=True)
        return jsonify({'refunded': False}), 500


# ==================================================
# GET /media/<filename>
#
# Before returning, g4f.client.Client().images.generate() has already synchronously
# downloaded the generated image to the local get_media_dir() directory (./generated_images
# preferred, otherwise ./generated_media), and set the Result DTO's url field to a relative
# path shaped like "/media/<filename>?url=<the original external address>" -- this is a
# routing convention registered by g4f's own bundled GUI/API server; this project does not
# run that server, so it must add this static file route itself, otherwise the frontend
# <img> tag and the download button requesting /media/<filename> would get a 404 (the
# download button would save the 404 error page as if it were image bytes, resulting in an
# "unsupported file format"). This only reads a file that has already been generated
# locally; it never fires any server-side fetch based on the url query parameter --
# consistent with the SSRF-avoidance principle that "the download button does no
# server-side proxying."
# ==================================================
@app.route('/media/<path:filename>')
def serve_generated_media(filename):
    safe_filename = os.path.basename(filename)
    media_dir = os.path.abspath(get_media_dir())
    return send_from_directory(media_dir, safe_filename)


# ==================================================
# Targeted local image file cleanup: only triggered when a user explicitly deletes an
# image_history record (not age-based lazy cleanup, and not wiping the whole directory, so
# this does not violate the "do not introduce an automatic cleanup mechanism" constraint --
# once the record is gone, these local files can never be referenced by any page again, so
# deleting them is safe, and it also frees up some of the disk usage that keeps growing
# under get_media_dir()). Handles both the url field of g4f image results (shaped like
# "/media/<filename>?url=...") and the local files saved via
# _persist_image_result_local_copy() when Gemini/ChatGPT's official image results are saved
# to history (shaped like "/media/<filename>"; since 2026-07-06 these two providers'
# persisted copies are no longer always None, see the comment above that function).
# ==================================================
def _delete_local_media_files_for_image_results(results):
    if not results:
        return
    media_dir = os.path.abspath(get_media_dir())
    for result in results:
        if result.get('type') not in ('g4f_image', 'openai_image', 'google_genai'):
            continue
        url = result.get('url')
        if not url:
            continue
        filename = os.path.basename(urlparse(url).path)
        if not filename:
            continue
        file_path = os.path.join(media_dir, filename)
        try:
            os.remove(file_path)
        except FileNotFoundError:
            pass
        except OSError as e:
            logger.warning(f"Could not delete generated media file {filename}: {str(e)}")


# ==================================================
# Chat history: paginated query
# GET /api/history?page=1&limit=20
# ==================================================
@app.route('/api/history', methods=['GET'])
def get_history():
    try:
        user_id, err_response = _get_authenticated_user_id()
        if err_response:
            return err_response

        page = request.args.get('page', 1, type=int) or 1
        limit = request.args.get('limit', 20, type=int) or 20
        page = max(page, 1)
        limit = max(min(limit, 100), 1)
        offset = (page - 1) * limit

        history_list = get_chat_history_list(user_id, limit=limit, offset=offset)
        return jsonify({
            'history': history_list,
            'page': page,
            'limit': limit
        })

    except Exception as e:
        logger.error(f"Error in get_history: {str(e)}", exc_info=True)
        return jsonify({
            'error': 'Service temporarily unavailable. Please try again later.'
        }), 500


# ==================================================
# Chat history: rename
# PATCH /api/history/<history_id>/title
# ==================================================
@app.route('/api/history/<history_id>/title', methods=['PATCH'])
def update_history_title(history_id):
    try:
        user_id, err_response = _get_authenticated_user_id()
        if err_response:
            return err_response

        data = request.get_json() or {}
        new_title = data.get('new_title', '').strip()
        if not new_title:
            return jsonify({'error': 'new_title is required'}), 400

        success = update_chat_history_title(user_id, history_id, new_title)
        if not success:
            return jsonify({'error': 'History entry not found'}), 404

        return jsonify({'status': 'ok'})

    except Exception as e:
        logger.error(f"Error in update_history_title: {str(e)}", exc_info=True)
        return jsonify({
            'error': 'Service temporarily unavailable. Please try again later.'
        }), 500


# ==================================================
# Chat history: delete
# DELETE /api/history/<history_id>
# ==================================================
@app.route('/api/history/<history_id>', methods=['DELETE'])
def delete_history(history_id):
    try:
        user_id, err_response = _get_authenticated_user_id()
        if err_response:
            return err_response

        success = delete_chat_history(user_id, history_id)
        if not success:
            return jsonify({'error': 'History entry not found'}), 404

        return jsonify({'status': 'ok'})

    except Exception as e:
        logger.error(f"Error in delete_history: {str(e)}", exc_info=True)
        return jsonify({
            'error': 'Service temporarily unavailable. Please try again later.'
        }), 500


# ==================================================
# Chat history: toggle pin state
# POST /api/history/<history_id>/toggle-pin
# ==================================================
@app.route('/api/history/<history_id>/toggle-pin', methods=['POST'])
def toggle_history_pin(history_id):
    try:
        user_id, err_response = _get_authenticated_user_id()
        if err_response:
            return err_response

        new_pinned = toggle_pin_chat_history(user_id, history_id)
        if new_pinned is None:
            return jsonify({'error': 'History entry not found'}), 404

        return jsonify({'is_pinned': new_pinned})

    except Exception as e:
        logger.error(f"Error in toggle_history_pin: {str(e)}", exc_info=True)
        return jsonify({
            'error': 'Service temporarily unavailable. Please try again later.'
        }), 500


# ==================================================
# Text-to-image history: paginated query
# GET /api/image-history?page=1&limit=20
#
# Mirrors the 4 chat history /api/history* routes point for point, likewise guarded by
# _get_authenticated_user_id() -- guests and anonymous users always get 401 (this guard
# doesn't distinguish "chat" from "image" to begin with, so it can be reused unchanged).
# ==================================================
@app.route('/api/image-history', methods=['GET'])
def get_image_history():
    try:
        user_id, err_response = _get_authenticated_user_id()
        if err_response:
            return err_response

        page = request.args.get('page', 1, type=int) or 1
        limit = request.args.get('limit', 20, type=int) or 20
        page = max(page, 1)
        limit = max(min(limit, 100), 1)
        offset = (page - 1) * limit

        history_list = get_image_history_list(user_id, limit=limit, offset=offset)
        return jsonify({
            'history': history_list,
            'page': page,
            'limit': limit
        })

    except Exception as e:
        logger.error(f"Error in get_image_history: {str(e)}", exc_info=True)
        return jsonify({
            'error': 'Service temporarily unavailable. Please try again later.'
        }), 500


# ==================================================
# Text-to-image history: rename
# PATCH /api/image-history/<history_id>/title
# ==================================================
@app.route('/api/image-history/<history_id>/title', methods=['PATCH'])
def update_image_history_title_route(history_id):
    try:
        user_id, err_response = _get_authenticated_user_id()
        if err_response:
            return err_response

        data = request.get_json() or {}
        new_title = data.get('new_title', '').strip()
        if not new_title:
            return jsonify({'error': 'new_title is required'}), 400

        success = update_image_history_title(user_id, history_id, new_title)
        if not success:
            return jsonify({'error': 'History entry not found'}), 404

        return jsonify({'status': 'ok'})

    except Exception as e:
        logger.error(f"Error in update_image_history_title_route: {str(e)}", exc_info=True)
        return jsonify({
            'error': 'Service temporarily unavailable. Please try again later.'
        }), 500


# ==================================================
# Text-to-image history: delete
# DELETE /api/image-history/<history_id>
# ==================================================
@app.route('/api/image-history/<history_id>', methods=['DELETE'])
def delete_image_history_route(history_id):
    try:
        user_id, err_response = _get_authenticated_user_id()
        if err_response:
            return err_response

        # Take a snapshot of results before deleting the Firestore record, used to locate
        # the local files that need cleaning up alongside it -- once the record is deleted,
        # there is no way to get back which filenames this generation referenced.
        entry = get_image_history_by_id(user_id, history_id)

        success = delete_image_history(user_id, history_id)
        if not success:
            return jsonify({'error': 'History entry not found'}), 404

        if entry:
            _delete_local_media_files_for_image_results(entry.get('results'))

        return jsonify({'status': 'ok'})

    except Exception as e:
        logger.error(f"Error in delete_image_history_route: {str(e)}", exc_info=True)
        return jsonify({
            'error': 'Service temporarily unavailable. Please try again later.'
        }), 500


# ==================================================
# Text-to-image history: toggle pin state
# POST /api/image-history/<history_id>/toggle-pin
# ==================================================
@app.route('/api/image-history/<history_id>/toggle-pin', methods=['POST'])
def toggle_image_history_pin_route(history_id):
    try:
        user_id, err_response = _get_authenticated_user_id()
        if err_response:
            return err_response

        new_pinned = toggle_pin_image_history(user_id, history_id)
        if new_pinned is None:
            return jsonify({'error': 'History entry not found'}), 404

        return jsonify({'is_pinned': new_pinned})

    except Exception as e:
        logger.error(f"Error in toggle_image_history_pin_route: {str(e)}", exc_info=True)
        return jsonify({
            'error': 'Service temporarily unavailable. Please try again later.'
        }), 500


# ==================================================
# Frontier model (Claude/Gemini) trial quota query
# GET /api/quota-status
#
# Used by the frontend navbar's "Trial Quota" badge (added 2026-07-05): the page already
# injects the initial value via index() on load (see that route); this endpoint is used to
# refresh the badge number after every call to /api/claude-chat or /api/gemini-image,
# avoiding having the frontend guess for itself "did this call consume quota" (a call using
# a personal key, or a failed call, consumes no quota; asking the backend for the real count
# directly is more reliable than replicating that decision logic on the frontend). Reuses
# the _get_authenticated_user_id() guard -- guests/anonymous users always get 401, because
# Claude/Gemini are simply not available to guests to begin with, so there is no quota to
# query (the same guard as the Claude/Gemini/chat/image history routes). The two counters
# are returned fully independently, sharing no quota with each other, consistent with the
# semantics recorded in CLAUDE.md sections 6/7.
# ==================================================
@app.route('/api/quota-status', methods=['GET'])
def quota_status():
    try:
        user_id, err_response = _get_authenticated_user_id()
        if err_response:
            return err_response

        return jsonify({
            'claude': {
                'used': get_claude_free_tier_usage(user_id),
                'limit': CLAUDE_FREE_TIER_LIMIT,
            },
            'gemini': {
                'used': get_gemini_free_tier_usage(user_id),
                'limit': GEMINI_FREE_TIER_LIMIT,
            },
            'chatgpt': {
                'used': get_free_tier_usage(user_id, CHATGPT_FREE_TIER_FIELD),
                'limit': CHATGPT_FREE_TIER_LIMIT,
            },
            'gemini_text': {
                'used': get_free_tier_usage(user_id, GEMINI_TEXT_FREE_TIER_FIELD),
                'limit': GEMINI_TEXT_FREE_TIER_LIMIT,
            },
            'chatgpt_image': {
                'used': get_free_tier_usage(user_id, CHATGPT_IMAGE_FREE_TIER_FIELD),
                'limit': CHATGPT_IMAGE_FREE_TIER_LIMIT,
            },
        })

    except Exception as e:
        logger.error(f"Error in quota_status: {str(e)}", exc_info=True)
        return jsonify({
            'error': 'Service temporarily unavailable. Please try again later.'
        }), 500


# ==================================================
# Health check
# ==================================================
@app.route('/health')
def health_check():
    return jsonify({
        'status': 'healthy',
        'g4f_available': G4F_AVAILABLE,
        'providers': [p.__name__ for p in G4F_PROVIDERS],
        'image_providers': [p.__name__ for p in IMAGE_PROVIDERS],
        'routing_rules_loaded': bool(ROUTE_PROMPTS_MAP),
        'peer_review_rules_loaded': bool(PEER_REVIEW_PROMPTS_MAP),
        'claude_available': CLAUDE_AVAILABLE,
        'claude_models': list(CLAUDE_MODELS.keys()),
        'gemini_available': GEMINI_AVAILABLE,
        'gemini_models': list(GEMINI_IMAGE_MODELS.keys()),
        'gemini_text_models': list(GEMINI_TEXT_MODELS.keys()),
        'chatgpt_available': CHATGPT_AVAILABLE,
        'chatgpt_models': list(CHATGPT_MODELS.keys()),
        'chatgpt_image_models': list(CHATGPT_IMAGE_MODELS.keys()),
        'timestamp': time.time()
    })


@app.route('/api/auth/guest', methods=['POST'])
def guest_login():
    session['is_guest'] = True
    return jsonify({'status': 'ok'})


@app.errorhandler(404)
def not_found(_error):
    return jsonify({'error': 'Not found'}), 404


@app.errorhandler(500)
def internal_error(error):
    logger.error(f"Internal server error: {error}")
    return jsonify({'error': 'Internal server error'}), 500


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    app.run(debug=False, host='0.0.0.0', port=port)