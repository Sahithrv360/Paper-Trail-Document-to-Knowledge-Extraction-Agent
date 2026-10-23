"""Optional local inference adapter for Katana ML's fine-tuned Donut model."""

MODEL_ID = "katanaml-org/invoices-donut-model-v1"
MODEL_REVISION = "1745f5a29c37bc5d2e16ab5e2bc6aba57fdd6f7c"
TASK_PROMPT = "<s_cord-v2>"
MAX_GENERATION_TOKENS = 1536


def load_model(device: str = "cpu"):
    try:
        import torch
        from transformers import DonutProcessor, VisionEncoderDecoderModel
    except ImportError as exc:
        raise RuntimeError(
            "Install optional extraction dependencies from backend/requirements-extraction.txt"
        ) from exc
    processor = DonutProcessor.from_pretrained(MODEL_ID, revision=MODEL_REVISION)
    model = VisionEncoderDecoderModel.from_pretrained(MODEL_ID, revision=MODEL_REVISION)
    model.to(device)
    model.eval()
    return processor, model


def extract_image(image, processor, model, device: str = "cpu") -> dict:
    """Compatibility API returning only the decoded Donut labels."""
    return extract_image_with_diagnostics(image, processor, model, device)["payload"]


def extract_image_with_diagnostics(image, processor, model, device: str = "cpu") -> dict:
    """Return decoded labels plus raw generation diagnostics for audit/review."""
    import torch

    from .schema import unpack_ground_truth

    image = image.convert("RGB")
    pixel_values = processor(image, return_tensors="pt").pixel_values.to(device)
    decoder_input_ids = processor.tokenizer(
        TASK_PROMPT, add_special_tokens=False, return_tensors="pt"
    ).input_ids.to(device)
    options = {
        "pixel_values": pixel_values,
        "decoder_input_ids": decoder_input_ids,
        # The pinned checkpoint's decoder has 1,536 positions. The previous 768
        # cap could truncate a long invoice table before the decoder reached EOS.
        "max_length": MAX_GENERATION_TOKENS,
        "pad_token_id": processor.tokenizer.pad_token_id,
        "eos_token_id": processor.tokenizer.eos_token_id,
        "use_cache": True,
    }
    unk_id = processor.tokenizer.unk_token_id
    if unk_id is not None:
        options["bad_words_ids"] = [[unk_id]]
    with torch.inference_mode():
        generated = model.generate(**options)
    raw_sequence = processor.tokenizer.batch_decode(generated, skip_special_tokens=False)[0]
    eos_id = processor.tokenizer.eos_token_id
    token_ids = generated[0].tolist()
    eos_observed = eos_id is not None and eos_id in token_ids
    generated_tokens = len(token_ids)
    reached_limit = generated_tokens >= MAX_GENERATION_TOKENS and not eos_observed
    sequence = raw_sequence
    sequence = sequence.replace(processor.tokenizer.pad_token, "")
    sequence = sequence.replace(processor.tokenizer.eos_token, "")
    if sequence.startswith(TASK_PROMPT):
        sequence = sequence[len(TASK_PROMPT):]
    decoded = processor.token2json(sequence.strip())
    if not isinstance(decoded, dict):
        raise ValueError("Donut output did not decode to an object")
    payload = unpack_ground_truth(decoded)
    return {
        "payload": payload,
        "raw_response": raw_sequence,
        "diagnostics": {
            "generation_tokens": generated_tokens,
            "generation_limit": MAX_GENERATION_TOKENS,
            "eos_observed": eos_observed,
            "generation_may_be_truncated": reached_limit,
            "decoded_item_count": len(payload.get("items", []))
            if isinstance(payload.get("items"), list) else 0,
        },
    }
