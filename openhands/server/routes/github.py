from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import JSONResponse
from pydantic import SecretStr

from openhands.integrations.github.github_service import GithubServiceImpl
from openhands.integrations.github.github_types import (
    GhAuthenticationError,
    GHUnknownException,
    GitHubRepository,
    GitHubUser,
    SuggestedTask,
)
from openhands.server.auth import get_token, get_token_type, get_idp_token, get_user_id

app = APIRouter(prefix='/api/github')


@app.get('/repositories')
async def get_github_repositories(
    request: Request,
    page: int = 1,
    per_page: int = 10,
    sort: str = 'pushed',
    installation_id: int | None = None,
    github_user_id: str | None = Depends(get_user_id),
    github_user_token: SecretStr | None = Depends(get_token),
    idp_token: SecretStr | None = Depends(get_idp_token),
) -> list[GitHubRepository] | JSONResponse:
    token = get_token(request)
    token_type = get_token_type(request)
    if token_type != 'github':
        return JSONResponse(
            content='Invalid token type. GitHub token required.',
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    client = GithubServiceImpl(
        user_id=github_user_id, idp_token=idp_token, token=token
    )
    try:
        repos: list[GitHubRepository] = await client.get_repositories(
            page, per_page, sort, installation_id
        )
        return repos

    except GhAuthenticationError as e:
        return JSONResponse(
            content=str(e),
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    except GHUnknownException as e:
        return JSONResponse(
            content=str(e),
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


@app.get('/user')
async def get_github_user(
    request: Request,
    github_user_id: str | None = Depends(get_user_id),
    github_user_token: SecretStr | None = Depends(get_token),
    idp_token: SecretStr | None = Depends(get_idp_token),
) -> GitHubUser | JSONResponse:
    token = get_token(request)
    token_type = get_token_type(request)
    if token_type != 'github':
        return JSONResponse(
            content='Invalid token type. GitHub token required.',
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    client = GithubServiceImpl(
        user_id=github_user_id, idp_token=idp_token, token=token
    )
    try:
        user: GitHubUser = await client.get_user()
        return user

    except GhAuthenticationError as e:
        return JSONResponse(
            content=str(e),
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    except GHUnknownException as e:
        return JSONResponse(
            content=str(e),
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


@app.get('/installations')
async def get_github_installation_ids(
    request: Request,
    github_user_id: str | None = Depends(get_user_id),
    github_user_token: SecretStr | None = Depends(get_token),
    idp_token: SecretStr | None = Depends(get_idp_token),
) -> list[int] | JSONResponse:
    token = get_token(request)
    token_type = get_token_type(request)
    if token_type != 'github':
        return JSONResponse(
            content='Invalid token type. GitHub token required.',
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    client = GithubServiceImpl(
        user_id=github_user_id, idp_token=idp_token, token=token
    )
    try:
        installations_ids: list[int] = await client.get_installation_ids()
        return installations_ids

    except GhAuthenticationError as e:
        return JSONResponse(
            content=str(e),
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    except GHUnknownException as e:
        return JSONResponse(
            content=str(e),
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


@app.get('/search/repositories')
async def search_github_repositories(
    request: Request,
    query: str,
    per_page: int = 5,
    sort: str = 'stars',
    order: str = 'desc',
    github_user_id: str | None = Depends(get_user_id),
    github_user_token: SecretStr | None = Depends(get_token),
    idp_token: SecretStr | None = Depends(get_idp_token),
) -> list[GitHubRepository] | JSONResponse:
    token = get_token(request)
    token_type = get_token_type(request)
    if token_type != 'github':
        return JSONResponse(
            content='Invalid token type. GitHub token required.',
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    client = GithubServiceImpl(
        user_id=github_user_id, idp_token=idp_token, token=token
    )
    try:
        repos: list[GitHubRepository] = await client.search_repositories(
            query, per_page, sort, order
        )
        return repos

    except GhAuthenticationError as e:
        return JSONResponse(
            content=str(e),
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    except GHUnknownException as e:
        return JSONResponse(
            content=str(e),
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


@app.get('/suggested-tasks')
async def get_suggested_tasks(
    request: Request,
    github_user_id: str | None = Depends(get_user_id),
    github_user_token: SecretStr | None = Depends(get_token),
    idp_token: SecretStr | None = Depends(get_idp_token),
) -> list[SuggestedTask] | JSONResponse:
    """Get suggested tasks for the authenticated user across their most recently pushed repositories.

    Returns:
    - PRs owned by the user
    - Issues assigned to the user.
    """
    token = get_token(request)
    token_type = get_token_type(request)
    if token_type != 'github':
        return JSONResponse(
            content='Invalid token type. GitHub token required.',
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    client = GithubServiceImpl(
        user_id=github_user_id, idp_token=idp_token, token=token
    )
    try:
        tasks: list[SuggestedTask] = await client.get_suggested_tasks()
        return tasks

    except GhAuthenticationError as e:
        return JSONResponse(
            content=str(e),
            status_code=401,
        )

    except GHUnknownException as e:
        return JSONResponse(
            content=str(e),
            status_code=500,
        )