"""Explicit Cloud setup: python -m app.llm.manage_prompts publish|check|migrate."""

import argparse

from sqlalchemy import select

from app.llm.prompt_registry import (
    CATALOG, PromptUnavailable, builtin_text, get_client,
    publish_user_prompt, remote_prompt, resolve_reference,
)


def publish(label: str) -> None:
    from langfuse.api import NotFoundError

    client = get_client()
    if client is None:
        raise PromptUnavailable("LANGFUSE_ENABLED=true를 .env에 설정하세요.")
    for name in CATALOG:
        content = builtin_text(name)
        try:
            current = remote_prompt(name, label="latest", cache_ttl_seconds=0)
        except PromptUnavailable as exc:
            if not isinstance(exc.__cause__, NotFoundError):
                raise
            current = None
        if current is not None and current.prompt == content:
            if label not in current.labels:
                client.update_prompt(name=name, version=current.version, new_labels=[label])
            version = current.version
        else:
            version = client.create_prompt(
                name=name, type="text", prompt=content, labels=[label],
                commit_message="Import Talky repository prompt",
            ).version
        print(f"{name}: v{version} [{label}]")


def migrate() -> int:
    """Verify remote bodies before attaching references; keep legacy bodies intact."""
    from app.db import repo
    from app.db.models import PromptVersion

    if get_client() is None:
        raise PromptUnavailable("LANGFUSE_ENABLED=true를 .env에 설정하세요.")
    with repo.session_scope() as session:
        ids = session.scalars(select(PromptVersion.id).where(
            PromptVersion.langfuse_name.is_(None),
        ).order_by(PromptVersion.user_id, PromptVersion.version)).all()
    migrated = 0
    for record_id in ids:
        with repo.session_scope() as session:
            row = session.scalars(select(PromptVersion).where(
                PromptVersion.id == record_id,
            ).with_for_update()).one()
            if row.langfuse_name is not None:
                continue
            reference = publish_user_prompt(
                str(row.user_id) if row.user_id else f"legacy-{row.id}",
                row.content, record_id=str(row.id),
            )
            row.langfuse_name = reference["name"]
            row.langfuse_version = reference["version"]
            session.flush()
            migrated += 1
    return migrated


def cache() -> int:
    from app.db.models import PromptVersion, Conversation
    from app.db.postgres import session_scope
    from app.settings import get_settings
    from app.llm.prompt_cache import save
    cfg = get_settings()
    versions = dict(cfg.LANGFUSE_PROMPT_VERSIONS)
    if not set(('talky/system', 'talky/ko-coach', 'talky/en-reply', 'talky/better-expression')) <= versions.keys():
        raise ValueError('Pin all four service prompts before caching a release')
    references = set(versions.items())
    with session_scope() as session:
        references.update(session.execute(select(PromptVersion.langfuse_name, PromptVersion.langfuse_version)
            .where(PromptVersion.langfuse_name.is_not(None))).all())
        for refs in session.scalars(select(Conversation.common_prompt_refs).where(Conversation.common_prompt_refs.is_not(None))):
            references.update((ref['name'], ref['version']) for ref in refs.values() if ref['source']=='langfuse')
    for name, version in sorted(references):
        prompt = remote_prompt(name, version=version, cache_ttl_seconds=0)
        save(name, version, prompt.prompt)
    return len(references)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("publish", "check", "migrate", "cache"))
    parser.add_argument("--label", choices=("staging", "production"), default="staging")
    args = parser.parse_args()
    client = get_client()
    if client is None or not client.auth_check():
        raise SystemExit("Langfuse 연결 설정을 확인하세요. 키는 .env에만 저장하세요.")
    if args.command == "publish":
        publish(args.label)
    elif args.command == "cache":
        print(f"Cached {cache()} exact managed prompt versions.")
    elif args.command == "migrate":
        print(f"Linked {migrate()} legacy prompt versions; legacy bodies were preserved.")
    else:
        for name in CATALOG:
            reference = resolve_reference(name)
            print(f"{name}: v{reference['version']}")
    client.flush()


if __name__ == "__main__":
    main()
