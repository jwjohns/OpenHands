import { OpenHandsActionEvent } from "./base";
import { ActionSecurityRisk } from "#/state/security-analyzer-slice";

export interface UserMessageAction extends OpenHandsActionEvent<"message"> {
  source: "user";
  args: {
    content: string;
    image_urls: string[];
  };
}

export interface CommandAction extends OpenHandsActionEvent<"run"> {
  source: "agent";
  args: {
    command: string;
    security_risk: ActionSecurityRisk;
    confirmation_state: "confirmed" | "rejected" | "awaiting_confirmation";
    thought: string;
    hidden?: boolean;
  };
}

export interface AssistantMessageAction
  extends OpenHandsActionEvent<"message"> {
  source: "agent";
  args: {
    thought: string;
    image_urls: string[] | null;
    wait_for_response: boolean;
  };
}

export interface IPythonAction extends OpenHandsActionEvent<"run_ipython"> {
  source: "agent";
  args: {
    code: string;
    security_risk: ActionSecurityRisk;
    confirmation_state: "confirmed" | "rejected" | "awaiting_confirmation";
    kernel_init_code: string;
    thought: string;
  };
}

export interface FinishAction extends OpenHandsActionEvent<"finish"> {
  source: "agent";
  args: {
    message_text: string;
    task_completed: "success" | "failure" | "partial";
    outputs: Record<string, unknown>;
    thought: string;
  };
}

export interface DelegateAction extends OpenHandsActionEvent<"delegate"> {
  source: "agent";
  timeout: number;
  args: {
    agent: "BrowsingAgent";
    inputs: Record<string, string>;
    thought: string;
  };
}

export interface BrowseAction extends OpenHandsActionEvent<"browse"> {
  source: "agent";
  args: {
    url: string;
    thought: string;
  };
}

export interface BrowseInteractiveAction
  extends OpenHandsActionEvent<"browse_interactive"> {
  source: "agent";
  timeout: number;
  args: {
    browser_actions: string;
    thought: string | null;
    browsergym_send_msg_to_user: string;
  };
}

export interface FileReadAction extends OpenHandsActionEvent<"read"> {
  source: "agent";
  args: {
    path: string;
    thought: string;
    security_risk: ActionSecurityRisk | null;
    impl_source?: string;
    view_range?: number[] | null;
  };
}

export interface FileWriteAction extends OpenHandsActionEvent<"write"> {
  source: "agent";
  args: {
    path: string;
    content: string;
    thought: string;
  };
}

export interface FileEditAction extends OpenHandsActionEvent<"edit"> {
  source: "agent";
  args: {
    path: string;
    command?: string;
    file_text?: string | null;
    view_range?: number[] | null;
    old_str?: string | null;
    new_str?: string | null;
    insert_line?: number | null;
    content?: string;
    start?: number;
    end?: number;
    thought: string;
    security_risk: ActionSecurityRisk | null;
    impl_source?: string;
  };
}

export interface RejectAction extends OpenHandsActionEvent<"reject"> {
  source: "agent";
  args: {
    thought: string;
  };
}

export type OpenHandsAction =
  | UserMessageAction
  | AssistantMessageAction
  | CommandAction
  | IPythonAction
  | FinishAction
  | DelegateAction
  | BrowseAction
  | BrowseInteractiveAction
  | FileReadAction
  | FileEditAction
  | FileWriteAction
  | RejectAction;
