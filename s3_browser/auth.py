"""AWS SSO and IAM authentication manager.

Supports the 4 required fields:
- start_url
- sso_region
- account_id
- role_name
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import webbrowser
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

import boto3
import botocore.exceptions
from botocore.session import Session as BotocoreSession

from s3_browser.config import SSOProfile


AWS_SSO_CACHE_DIR = Path.home() / ".aws" / "sso" / "cache"


@dataclass
class AuthCredentials:
    access_key_id: str
    secret_access_key: str
    session_token: str
    expiration: Optional[datetime] = None


class SSOAuthManager:
    """Manages AWS SSO device authorization, token caching, and role credentials."""

    def __init__(self, profile: SSOProfile) -> None:
        self.profile = profile
        AWS_SSO_CACHE_DIR.mkdir(parents=True, exist_ok=True)

    def _get_cache_key(self) -> str:
        """Standard SHA1 hash of start_url used by AWS CLI."""
        return hashlib.sha1(self.profile.start_url.encode("utf-8")).hexdigest()

    def _get_cache_file(self) -> Path:
        return AWS_SSO_CACHE_DIR / f"{self._get_cache_key()}.json"

    def get_cached_token(self) -> Optional[str]:
        """Loads cached SSO access token if present and not expired."""
        cache_file = self._get_cache_file()
        if not cache_file.exists():
            # Check all json files in cache dir in case SHA1 differed
            for p in AWS_SSO_CACHE_DIR.glob("*.json"):
                try:
                    with open(p, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        if data.get("startUrl") == self.profile.start_url:
                            exp_str = data.get("expiresAt")
                            if exp_str:
                                # Example: 2026-09-29T10:00:00UTC or 2026-09-29T10:00:00Z
                                exp_str_clean = exp_str.replace("UTC", "+00:00").replace("Z", "+00:00")
                                exp_time = datetime.fromisoformat(exp_str_clean)
                                if exp_time > datetime.now(timezone.utc):
                                    return data.get("accessToken")
                except Exception:
                    continue
            return None

        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                exp_str = data.get("expiresAt")
                if exp_str:
                    exp_str_clean = exp_str.replace("UTC", "+00:00").replace("Z", "+00:00")
                    exp_time = datetime.fromisoformat(exp_str_clean)
                    if exp_time > datetime.now(timezone.utc):
                        return data.get("accessToken")
        except Exception:
            return None
        return None

    def start_device_login(
        self,
        status_callback: Optional[Callable[[str], None]] = None,
        cancel_check: Optional[Callable[[], bool]] = None,
    ) -> str:
        """Runs the AWS SSO OIDC device authorization flow.

        Registers client, gets device code, opens browser, and polls for token.
        """
        oidc = boto3.client("sso-oidc", region_name=self.profile.sso_region)

        if status_callback:
            status_callback("Registering SSO client...")

        client_info = oidc.register_client(
            clientName="S3BrowserBulkDownloader",
            clientType="public",
        )
        client_id = client_info["clientId"]
        client_secret = client_info["clientSecret"]

        if status_callback:
            status_callback("Requesting SSO device authorization...")

        auth_info = oidc.start_device_authorization(
            clientId=client_id,
            clientSecret=client_secret,
            startUrl=self.profile.start_url,
        )

        verification_uri = auth_info.get("verificationUriComplete") or auth_info.get("verificationUri")
        user_code = auth_info.get("userCode")
        device_code = auth_info["deviceCode"]
        interval = auth_info.get("interval", 5)
        expires_in = auth_info.get("expiresIn", 300)
        expires_at = time.time() + expires_in

        if status_callback:
            status_callback(f"Opening browser for SSO confirmation (Code: {user_code})...")

        if verification_uri:
            webbrowser.open(verification_uri)

        # Poll for token approval
        while time.time() < expires_at:
            if cancel_check and cancel_check():
                raise RuntimeError("SSO Login canceled by user.")

            time.sleep(interval)

            try:
                token_res = oidc.create_token(
                    clientId=client_id,
                    clientSecret=client_secret,
                    grantType="urn:ietf:params:oauth:grant-type:device_code",
                    deviceCode=device_code,
                )
                access_token = token_res["accessToken"]
                expires_in_sec = token_res.get("expiresIn", 28800)
                expires_at_dt = datetime.fromtimestamp(time.time() + expires_in_sec, timezone.utc)

                # Cache token
                cache_data = {
                    "startUrl": self.profile.start_url,
                    "region": self.profile.sso_region,
                    "accessToken": access_token,
                    "expiresAt": expires_at_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
                }
                cache_file = self._get_cache_file()
                with open(cache_file, "w", encoding="utf-8") as f:
                    json.dump(cache_data, f, indent=2)

                if status_callback:
                    status_callback("SSO Authentication successful!")

                return access_token

            except oidc.exceptions.AuthorizationPendingException:
                if status_callback:
                    status_callback("Waiting for approval in browser...")
                continue
            except oidc.exceptions.SlowDownException:
                interval += 5
                time.sleep(interval)
            except Exception as e:
                raise RuntimeError(f"SSO Token request failed: {e}")

        raise TimeoutError("SSO Device authorization timed out.")

    def get_role_credentials(
        self,
        access_token: Optional[str] = None,
        force_login: bool = False,
        status_callback: Optional[Callable[[str], None]] = None,
        cancel_check: Optional[Callable[[], bool]] = None,
    ) -> AuthCredentials:
        """Retrieves temporary AWS credentials via sso:GetRoleCredentials."""
        token = access_token
        if not token and not force_login:
            token = self.get_cached_token()

        if not token:
            token = self.start_device_login(
                status_callback=status_callback, cancel_check=cancel_check
            )

        if status_callback:
            status_callback("Acquiring AWS role credentials...")

        sso = boto3.client("sso", region_name=self.profile.sso_region)
        try:
            res = sso.get_role_credentials(
                roleName=self.profile.role_name,
                accountId=self.profile.account_id,
                accessToken=token,
            )
        except botocore.exceptions.ClientError as e:
            code = e.response.get("Error", {}).get("Code", "")
            # If expired token error, retry once with fresh login
            if code in ("UnauthorizedException", "ExpiredTokenException") and not force_login:
                if status_callback:
                    status_callback("SSO session expired, refreshing login...")
                token = self.start_device_login(
                    status_callback=status_callback, cancel_check=cancel_check
                )
                res = sso.get_role_credentials(
                    roleName=self.profile.role_name,
                    accountId=self.profile.account_id,
                    accessToken=token,
                )
            else:
                raise

        creds = res["roleCredentials"]
        exp_ms = creds.get("expiration")
        exp_dt = datetime.fromtimestamp(exp_ms / 1000.0, timezone.utc) if exp_ms else None

        return AuthCredentials(
            access_key_id=creds["accessKeyId"],
            secret_access_key=creds["secretAccessKey"],
            session_token=creds["sessionToken"],
            expiration=exp_dt,
        )

    def create_boto3_session(
        self,
        force_login: bool = False,
        status_callback: Optional[Callable[[str], None]] = None,
        cancel_check: Optional[Callable[[], bool]] = None,
    ) -> boto3.Session:
        """Creates an authenticated boto3.Session using the SSO profile credentials."""
        creds = self.get_role_credentials(
            force_login=force_login,
            status_callback=status_callback,
            cancel_check=cancel_check,
        )
        return boto3.Session(
            aws_access_key_id=creds.access_key_id,
            aws_secret_access_key=creds.secret_access_key,
            aws_session_token=creds.session_token,
            region_name=self.profile.default_region or self.profile.sso_region,
        )
