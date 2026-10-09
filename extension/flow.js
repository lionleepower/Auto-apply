// 一次「填写本页」的完整流程：上传简历 → 补齐重复段落 → 问本地服务填什么 → 逐个填写 → 标记。
// 只依赖传进来的 env，不直接用 chrome.* —— 插件用 chrome.scripting 实现 env，测试用 Playwright 实现，跑的是同一份代码。
//   env.inject()            把 autofill.js 注入页面
//   env.exec(func, args)    在页面里执行 func(...args)，返回结果（func 必须自包含，不能引用外部变量）
//   env.api(path, body?)    调本地服务，body 为空时用 GET
//   env.progress(text)      更新进度
//   env.click(x, y)         （可选）在页面坐标处做真实点击；没有就只用模拟点击
// opts.shouldStop()：返回 true 时停下来，已经填了的照常出报告
(() => {
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  // ---- 以下函数会被序列化后送进页面执行，必须自包含 ----
  function pageExtract() {
    return window.__autofill.extract();
  }
  function pageClickAdd(id) {
    return window.__autofill.clickAdd(id);
  }
  function pageStartJob(id, kind, candidates, meta, trusted) {
    return window.__autofill.startJob(id, kind, candidates, meta, trusted);
  }
  function pagePoll() {
    return window.__autofill.pollJob();
  }
  function pageAck() {
    return window.__autofill.ackClick();
  }
  function pageExpanders() {
    return window.__autofill.findExpanders();
  }
  function pageHidden() {
    return document.hidden;
  }
  function pageSaveCount() {
    return window.__autofill.saveButtons().length;
  }
  function pageMarkAll(rows) {
    rows.forEach(([id, status, note, risk]) => window.__autofill.mark(id, status, note, risk));
    return rows.length;
  }
  function pageSetFile(id, b64, name, mime) {
    const el = document.querySelector('[data-af-id="' + id + '"]');
    if (!el) return 0;
    const bin = atob(b64);
    const bytes = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
    const dt = new DataTransfer();
    dt.items.add(new File([bytes], name, { type: mime }));
    el.files = dt.files;
    el.dispatchEvent(new Event('input', { bubbles: true }));
    el.dispatchEvent(new Event('change', { bubbles: true }));
    return el.files.length;
  }

  const stopped = (opts) => !!(opts.shouldStop && opts.shouldStop());

  // 填一个字段：页面要点击时由这里做真实点击（env.click），Element Plus 等不认脚本模拟的点击
  async function runJob(env, target, kind, candidates, meta) {
    await env.exec(pageStartJob, [target, kind, candidates || [], meta || {}, !!(env.click && env.type && env.key)]);
    const deadline = Date.now() + 45000;
    while (Date.now() < deadline) {
      const st = await env.exec(pagePoll, []);
      if (st && st.click) {
        await env.click(st.click.x, st.click.y);
        await env.exec(pageAck, []);
        continue;
      }
      if (st && st.key) {
        await env.key(st.key);
        await env.exec(pageAck, []);
        continue;
      }
      if (st && st.text !== undefined) {
        await env.type(st.text);
        await env.exec(pageAck, []);
        continue;
      }
      if (st && st.done) return st.result || { ok: false, reason: '页面没有返回结果' };
      await sleep(30);
    }
    return { ok: false, reason: '超过 45 秒没有完成' };
  }

  async function run(env, opts) {
    const extract = () => env.exec(pageExtract, []);
    let uploadNote = null;
    let uploaded = false;
    await env.inject();

    if (opts.upload && !opts.dryRun) {
      env.progress('上传简历…');
      const target = await env.api('/resume-target', { fields: (await extract()).fields });
      if (!target.id) {
        uploadNote = '没找到简历上传框';
      } else {
        const file = await env.api('/resume', { resume: opts.resume || null });
        uploaded = (await env.exec(pageSetFile, [target.id, file.b64, file.name, file.mime])) > 0;
        uploadNote = uploaded ? `已上传简历：${file.name}` : '简历上传失败';
        if (uploaded) {
          env.progress('等网站解析简历…');
          await sleep(opts.parseWaitMs ?? 5000);
        }
      }
    }

    if (opts.portfolio && !opts.dryRun) {
      const target = await env.api('/portfolio-target', { fields: (await extract()).fields });
      if (!target.id) {
        uploadNote = [uploadNote, '没找到作品集上传框'].filter(Boolean).join('；');
      } else {
        env.progress('上传作品集…');
        try {
          const file = await env.api('/portfolio', { resume: opts.resume || null });
          const ok = (await env.exec(pageSetFile, [target.id, file.b64, file.name, file.mime])) > 0;
          uploadNote = [uploadNote, ok ? `已上传作品集：${file.name}` : '作品集上传失败'].filter(Boolean).join('；');
        } catch (e) {
          uploadNote = [uploadNote, `作品集上传失败：${e.message || e}`].filter(Boolean).join('；');
        }
      }
    }

    // 多轮：分区编辑式表单（OPPO）要逐个打开分区；选了「有」之后会冒出新字段
    const sessions = [];
    const results = {};
    const failed = [];  // 没填上的字段：标签页在后台时弹层不定位，有的要等页面显示出来再试一次
    const sections = [];
    const opened = [];
    let known = new Set();
    for (let round = 0; round < 12; round++) {
      if (stopped(opts)) break;
      let data = await extract();
      if (!opts.dryRun) {
        let stuck = [];
        let last = null;
        for (let i = 0; i < 20; i++) {
          if (stopped(opts)) break;
          const r = await env.api('/expand', { data, stuck, last, resume: opts.resume || null });
          stuck = r.stuck;
          if (!r.click) break;
          env.progress(`点「${r.click.text}」补齐段落…`);
          await env.exec(pageClickAdd, [r.click.id]);
          await sleep(opts.settleMs ?? 350);
          data = await extract();
          last = { sec: r.click.sec, before: r.click.before };
        }
      }

      env.progress('识别字段…');
      const plan = await env.api('/plan', {
        url: opts.url, fields: data.fields, round, adapters: data.adapters || [],
        options: {
          overwrite: !!opts.overwrite, dry_run: !!opts.dryRun, llm: opts.llm || 'none',
          referral: opts.referral || '', resume_uploaded: uploaded, resume: opts.resume || null,
        },
      });
      sessions.push(plan.session);
      plan.ids.forEach((id) => known.add(id));
      for (let i = 0; i < plan.fill.length; i++) {
        const f = plan.fill[i];
        if (stopped(opts)) {
          results[`${round}:${f.id}`] = { ok: false, reason: '已手动停止' };
          continue;
        }
        env.progress(`填写 ${i + 1}/${plan.fill.length}：${f.label || '（无标签）'}`);
        try {
          let r = await runJob(env, f.id, f.kind, f.candidates, f.meta);
          // 选项对不上（封闭列表）时，让 AI 从选项里挑最接近的一项再填一次；只发这一条资料值，结果标黄
          if (!r.ok && r.options && r.options.length && opts.llm && opts.llm !== 'none' && opts.aiPick !== false) {
            try {
              const p = await env.api('/pick-option', { session: plan.session, id: f.id, options: r.options });
              if (p.choice) {
                const r2 = await runJob(env, f.id, f.kind, [p.choice], f.meta);
                if (r2.ok) r = { ...r2, guessed: true, note: [r2.note, `AI 从选项里选了「${p.choice}」，请确认`].filter(Boolean).join('；') };
              }
            } catch (e) { /* AI 不可用：保留规则的失败原因 */ }
          }
          results[`${round}:${f.id}`] = r;
          if (!r.ok) failed.push({ key: `${round}:${f.id}`, f });
        } catch (e) {
          results[`${round}:${f.id}`] = { ok: false, reason: String((e && e.message) || e) };
        }
      }
      if (opts.dryRun) break;

      // 刚才的填写让新字段冒出来了（「工作经历」选了「有」才展开）：先填它们
      const after = await extract();
      if (after.fields.some((f) => !known.has(f.id))) continue;

      if (opts.save && (await env.exec(pageSaveCount, []))) {
        env.progress('保存这个分区…');
        const r = await runJob(env, '', '__save');
        const rec = { saved: r.sectionSaved ?? r.remaining === 0, errors: r.errors || [], dialogs: r.dialogs || [] };
        if (sections.length && sections[sections.length - 1].saved === null) Object.assign(sections[sections.length - 1], rec);
        else sections.push({ section: '页面', ...rec });
      }
      const exps = await env.exec(pageExpanders, []);
      const nxt = await env.api('/next-section', { expanders: exps || [], opened, resume: opts.resume || null });
      if (!nxt.id) break;
      opened.push(nxt.section);
      env.progress(`打开「${nxt.section}」…`);
      await runJob(env, nxt.id, '__expand');
      await sleep(1200);
      sections.push({ section: nxt.section, saved: null, errors: [] });
    }

    // 标签页在后台时浏览器暂停页面的动画帧，靠脚本定位的弹层（讯飞地区选择器）停在原地、点不到。
    // 这些字段等你切到这个标签页再补填一次；不切过去也不影响已经填好的部分，点「停止」直接出报告
    const retry = failed.filter((x) => !(results[x.key] && results[x.key].ok));
    if (!opts.dryRun && retry.length && await env.exec(pageHidden, [])) {
      env.progress(`有 ${retry.length} 项要在页面显示时才能选：切到这个标签页就会自动继续`);
      while (!stopped(opts) && await env.exec(pageHidden, [])) await sleep(500);
      for (let i = 0; i < retry.length && !stopped(opts); i++) {
        const { key, f } = retry[i];
        env.progress(`补填 ${i + 1}/${retry.length}：${f.label || '（无标签）'}`);
        try {
          const r = await runJob(env, f.id, f.kind, f.candidates, f.meta);
          if (r.ok) results[key] = r;
        } catch (e) { /* 保留第一次的失败原因 */ }
      }
    }

    const report = await env.api('/finish', { sessions, results, upload_note: uploadNote,
      sections: sections.filter((x) => x.saved !== null || !opts.save) });
    if (!opts.dryRun) await env.exec(pageMarkAll, [report.items.map((it) => [it.id, it.status, it.note, it.risk || ''])]);
    return report;
  }

  globalThis.AutofillFlow = { run };
})();
