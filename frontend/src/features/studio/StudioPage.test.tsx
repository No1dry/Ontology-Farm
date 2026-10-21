import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import StudioPage from "./StudioPage";
import { sessionController, type Session } from "../../api/session";
const session: Session = {
  user: {
    id: "alice",
    issuer: "test",
    subject: "alice",
    display_name: "Alice",
    email: null,
    enabled: true,
    revision: 1,
  },
  permissions: [],
  authorization_version: 1,
  csrf_token: "csrf",
};
const document = {
  classes: [
    {
      iri: "urn:Sensor",
      label: "Sensor",
      parents: [],
      description: "",
      deprecated: false,
    },
  ],
  properties: [],
  relations: [],
  shapes: "",
  dependencies: [],
  ui_metadata: {},
};
afterEach(() => {
  cleanup();
  sessionController.receive("logout");
  history.replaceState(null, "", "/");
});

async function openSavedDraft(
  read?: (id: string) => Promise<Response>,
  properties: unknown[] = [],
  create?: () => Promise<Response>,
) {
  const draft = (id: string) => ({
    id,
    revision: 1,
    base_release_id: null,
    document: {
      ...document,
      properties,
      classes: [{ ...document.classes[0], label: id }],
    },
    state: "draft",
  });
  sessionController.transport = (async (request: Request) => {
    const url = new URL(request.url);
    if (url.pathname.endsWith("/session")) return Response.json(session);
    if (url.pathname.endsWith("/releases"))
      return Response.json({ items: [], next_cursor: null });
    if (url.pathname.endsWith("/drafts"))
      return request.method === "POST" && create
        ? create()
        : Response.json({
            items: [draft("A"), draft("B"), draft("C")],
            next_cursor: null,
          });
    if (url.pathname.endsWith("/form-schema"))
      return Response.json({
        class_iri: "urn:Sensor",
        release_id: null,
        fields: [],
        inherited_from: {},
      });
    const id = url.pathname.split("/").at(-1)!;
    return read && id !== "A" ? read(id) : Response.json(draft(id));
  }) as typeof fetch;
  await sessionController.reconcile();
  history.replaceState(null, "", "/ontology/studio?draft=A");
  render(<StudioPage />);
  await screen.findByLabelText("中文名称");
  return draft;
}
it.each(['{"pending":true}', "{invalid"])(
  "protects pending metadata text %s as unsent work until explicitly discarded",
  async (text) => {
    await openSavedDraft();
    fireEvent.change(screen.getByLabelText("UI 元数据"), {
      target: { value: text },
    });
    if (text === "{invalid")
      fireEvent.click(screen.getByRole("button", { name: "应用UI 元数据" }));
    expect(
      (screen.getByLabelText("服务器草稿") as HTMLSelectElement).disabled,
    ).toBe(true);
    expect(
      (screen.getByRole("button", { name: "新建草稿" }) as HTMLButtonElement)
        .disabled,
    ).toBe(true);
    expect(screen.queryByText("已保存到服务器", { exact: true })).toBeNull();
    const leaving = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(leaving);
    expect(leaving.defaultPrevented).toBe(true);
    fireEvent.click(
      screen.getByRole("button", { name: "丢弃UI 元数据未应用文本" }),
    );
    expect(
      (screen.getByLabelText("服务器草稿") as HTMLSelectElement).disabled,
    ).toBe(false);
    fireEvent.change(screen.getByLabelText("服务器草稿"), {
      target: { value: "B" },
    });
    await waitFor(() =>
      expect(
        (screen.getByLabelText("中文名称") as HTMLInputElement).value,
      ).toBe("B"),
    );
    expect(
      (screen.getByLabelText("UI 元数据") as HTMLTextAreaElement).value,
    ).toBe("{}");
  },
);
it("retains a full-document buffer and refuses to overwrite later structured edits", async () => {
  await openSavedDraft();
  const full = screen.getByLabelText(
    "完整 SchemaDocument",
  ) as HTMLTextAreaElement;
  fireEvent.change(full, {
    target: {
      value: JSON.stringify({ ...document, ui_metadata: { pending: true } }),
    },
  });
  fireEvent.change(screen.getByLabelText("中文名称"), {
    target: { value: "later edit" },
  });
  expect(full.value).toContain('"pending":true');
  expect(
    (
      screen.getByRole("button", {
        name: "应用完整 SchemaDocument",
      }) as HTMLButtonElement
    ).disabled,
  ).toBe(true);
  expect((screen.getByLabelText("中文名称") as HTMLInputElement).value).toBe(
    "later edit",
  );
});
it("retains unapplied enum text by property without applying it to another property", async () => {
  await openSavedDraft(
    undefined,
    ["one", "two"].map((name) => ({
      iri: `urn:${name}`,
      label: name,
      datatype_iri: "http://www.w3.org/2001/XMLSchema#string",
      owner_policy_iri: "urn:manual",
      enum_values: [],
      superproperties: [],
      unit: null,
    })),
  );
  const select = screen.getByLabelText("选择数据属性");
  fireEvent.change(select, { target: { value: "urn:one" } });
  const text = '[{"kind":"literal","value":"one only"}]';
  fireEvent.change(screen.getByLabelText("枚举 RDF Terms"), {
    target: { value: text },
  });
  fireEvent.change(select, { target: { value: "urn:two" } });
  expect(
    (screen.getByLabelText("枚举 RDF Terms") as HTMLTextAreaElement).value,
  ).toBe("[]");
  fireEvent.click(screen.getByRole("button", { name: "应用枚举 RDF Terms" }));
  fireEvent.change(select, { target: { value: "urn:one" } });
  expect(
    (screen.getByLabelText("枚举 RDF Terms") as HTMLTextAreaElement).value,
  ).toBe(text);
  fireEvent.click(screen.getByRole("button", { name: "应用枚举 RDF Terms" }));
  const full = JSON.parse(
    (screen.getByLabelText("完整 SchemaDocument") as HTMLTextAreaElement).value,
  );
  expect(full.properties[0].enum_values[0].value).toBe("one only");
  expect(full.properties[1].enum_values).toEqual([]);
});
it("retains a JSON buffer typed during a delayed selection read", async () => {
  let resolve!: (response: Response) => void;
  const draft = await openSavedDraft(
    () =>
      new Promise((r) => {
        resolve = r;
      }),
  );
  fireEvent.change(screen.getByLabelText("服务器草稿"), {
    target: { value: "B" },
  });
  fireEvent.change(screen.getByLabelText("UI 元数据"), {
    target: { value: "{invalid" },
  });
  await act(async () => resolve(Response.json(draft("B"))));
  expect(
    (screen.getByLabelText("UI 元数据") as HTMLTextAreaElement).value,
  ).toBe("{invalid");
  expect(location.search).toBe("?draft=A");
});
it("keeps a removed property buffer visible for an explicit discard", async () => {
  await openSavedDraft(undefined, [
    {
      iri: "urn:one",
      label: "one",
      datatype_iri: "http://www.w3.org/2001/XMLSchema#string",
      owner_policy_iri: "urn:manual",
      enum_values: [],
      superproperties: [],
      unit: null,
    },
  ]);
  fireEvent.change(screen.getByLabelText("选择数据属性"), {
    target: { value: "urn:one" },
  });
  fireEvent.change(screen.getByLabelText("枚举 RDF Terms"), {
    target: { value: "{invalid enum" },
  });
  fireEvent.change(screen.getByLabelText("完整 SchemaDocument"), {
    target: { value: JSON.stringify(document) },
  });
  fireEvent.click(
    screen.getByRole("button", { name: "应用完整 SchemaDocument" }),
  );
  expect(screen.queryByLabelText("枚举 RDF Terms")).toBeNull();
  expect(screen.getByText("{invalid enum", { exact: true })).toBeDefined();
  fireEvent.click(screen.getByRole("button", { name: /丢弃保留文本/ }));
  expect(screen.queryByText("{invalid enum", { exact: true })).toBeNull();
  expect(
    (screen.getByRole("button", { name: "保存草稿" }) as HTMLButtonElement)
      .disabled,
  ).toBe(false);
});
it("does not replace local edits made during a delayed selection read", async () => {
  let resolve!: (response: Response) => void;
  const draft = await openSavedDraft(
    () =>
      new Promise((r) => {
        resolve = r;
      }),
  );
  fireEvent.change(screen.getByLabelText("服务器草稿"), {
    target: { value: "B" },
  });
  fireEvent.change(screen.getByLabelText("中文名称"), {
    target: { value: "keep A" },
  });
  await act(async () => resolve(Response.json(draft("B"))));
  expect((screen.getByLabelText("中文名称") as HTMLInputElement).value).toBe(
    "keep A",
  );
  expect(location.search).toBe("?draft=A");
});
it("does not discard edits made during a delayed new-draft command", async () => {
  let resolve!: (response: Response) => void;
  const draft = await openSavedDraft(
    undefined,
    [],
    () =>
      new Promise((r) => {
        resolve = r;
      }),
  );
  fireEvent.click(screen.getByRole("button", { name: "新建草稿" }));
  await waitFor(() => expect(resolve).toBeTypeOf("function"));
  fireEvent.change(screen.getByLabelText("中文名称"), {
    target: { value: "keep A" },
  });
  await act(async () => resolve(Response.json(draft("new"))));
  expect((screen.getByLabelText("中文名称") as HTMLInputElement).value).toBe(
    "keep A",
  );
  expect(location.search).toBe("?draft=A");
});
it("accepts only the latest requested draft when reads finish out of order", async () => {
  const reads: Record<string, (response: Response) => void> = {};
  const draft = await openSavedDraft(
    (id) =>
      new Promise((r) => {
        reads[id] = r;
      }),
  );
  fireEvent.change(screen.getByLabelText("服务器草稿"), {
    target: { value: "B" },
  });
  fireEvent.change(screen.getByLabelText("服务器草稿"), {
    target: { value: "C" },
  });
  await act(async () => reads.C(Response.json(draft("C"))));
  await act(async () => reads.B(Response.json(draft("B"))));
  expect((screen.getByLabelText("中文名称") as HTMLInputElement).value).toBe(
    "C",
  );
  expect(location.search).toBe("?draft=C");
});
it("retains local edits through same-user suspension, gates reads, and never marks an offline save acknowledged", async () => {
  let requests = 0;
  let offline = false;
  let unavailable = false;
  sessionController.transport = (async (request: Request) => {
    requests++;
    if (request.url.endsWith("/session"))
      return unavailable
        ? Response.json({ code: "OIDC_UNAVAILABLE" }, { status: 503 })
        : Response.json(session);
    if (offline) throw new TypeError("Offline");
    if (request.url.endsWith("/releases"))
      return Response.json({ items: [], next_cursor: null });
    if (request.url.includes("/form-schema"))
      return Response.json({
        class_iri: "urn:Sensor",
        release_id: null,
        fields: [],
        inherited_from: {},
      });
    if (request.url.endsWith("/drafts"))
      return Response.json({ items: [], next_cursor: null });
    return Response.json({
      id: "draft",
      revision: 1,
      base_release_id: null,
      document,
      state: "draft",
    });
  }) as typeof fetch;
  await sessionController.reconcile();
  history.replaceState(null, "", "/ontology/studio?draft=draft");
  render(<StudioPage />);
  const name = await screen.findByLabelText("中文名称");
  fireEvent.change(name, { target: { value: "本地未发送" } });
  act(() => sessionController.suspend());
  const suspendedRequests = requests;
  await new Promise((resolve) => setTimeout(resolve, 20));
  expect(requests).toBe(suspendedRequests);
  expect((name as HTMLInputElement).value).toBe("本地未发送");
  await act(async () => {
    await sessionController.reconcile();
  });
  expect((screen.getByLabelText("中文名称") as HTMLInputElement).value).toBe(
    "本地未发送",
  );
  unavailable = true;
  await act(async () => {
    await sessionController.reconcile();
  });
  expect(sessionController.state.status).toBe("unavailable");
  const unavailableRequests = requests;
  await new Promise((resolve) => setTimeout(resolve, 20));
  expect(requests).toBe(unavailableRequests);
  unavailable = false;
  await act(async () => {
    await sessionController.reconcile();
  });
  expect((screen.getByLabelText("中文名称") as HTMLInputElement).value).toBe(
    "本地未发送",
  );
  offline = true;
  fireEvent.click(screen.getByRole("button", { name: "保存草稿" }));
  await waitFor(() =>
    expect(
      screen.getByRole("button", {
        name: "重试原保存命令（相同请求与幂等键）",
      }),
    ).toBeDefined(),
  );
  expect(screen.queryByText("已保存到服务器", { exact: true })).toBeNull();
  expect((screen.getByLabelText("中文名称") as HTMLInputElement).value).toBe(
    "本地未发送",
  );
});
it("does not request saved inheritance for a class that only exists in the local draft", async () => {
  const requested: string[] = [];
  sessionController.transport = (async (request: Request) => {
    requested.push(request.url);
    if (request.url.endsWith("/session")) return Response.json(session);
    if (request.url.endsWith("/releases") || request.url.endsWith("/drafts"))
      return Response.json({ items: [], next_cursor: null });
    if (request.url.includes("/form-schema"))
      return Response.json({
        class_iri: "urn:Sensor",
        release_id: null,
        fields: [],
        inherited_from: {},
      });
    return Response.json({
      id: "draft",
      revision: 1,
      base_release_id: null,
      document,
      state: "draft",
    });
  }) as typeof fetch;
  await sessionController.reconcile();
  history.replaceState(null, "", "/ontology/studio?draft=draft");
  render(<StudioPage />);
  await screen.findByLabelText("中文名称");
  fireEvent.click(screen.getByRole("button", { name: "新建类" }));
  expect(screen.getByLabelText("父类", { exact: true })).toBeDefined();
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 20));
  });
  expect(
    requested.some(
      (url) => url.includes("/form-schema") && !url.includes("urn%3ASensor"),
    ),
  ).toBe(false);
});
