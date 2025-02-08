import { useNavigate, useSearchParams } from "react-router";
import { useQuery } from "@tanstack/react-query";
import React from "react";
import OpenHands from "#/api/open-hands";
import { useAuth } from "#/context/auth-context";

function OAuthGitHubCallback() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const code = searchParams.get("code");
  const requesterUrl = new URL(window.location.href);
  const redirectUrl = `${requesterUrl.origin}/oauth/github/callback`;
  const { setGitHubTokenIsSet } = useAuth();

  const { data, isSuccess, error } = useQuery({
    queryKey: ["access_token", code, redirectUrl],
    queryFn: () => OpenHands.getGitHubAccessToken(code!, redirectUrl),
    enabled: !!code,
  });

  React.useEffect(() => {
    if (isSuccess) {
      setGitHubTokenIsSet(true);
      navigate("/");
    }
  }, [isSuccess]);

  if (error) {
    return (
      <div>
        <h1>Error</h1>
        <p>{error.message}</p>
      </div>
    );
  }

  return (
    <div>
      <h1>Redirecting...</h1>
    </div>
  );
}

export default OAuthGitHubCallback;
