/*
 * MiniMD — 聊天气泡用的轻量 Markdown 渲染器
 * 支持:加粗 / 斜体 / 删除线 / 行内代码 / 代码块 / 链接 / 无序有序列表 / 标题 / 引用
 * 流式友好:未闭合的标记按原文显示,不破坏布局;输入全部转义,防注入。
 */
(function (global) {
  'use strict';

  function escapeHtml(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function fmtText(s) {
    s = escapeHtml(s);
    s = s.replace(/\[([^\]\n]+)\]\((https?:\/\/[^\s)]+)\)/g, function (m, text, url) {
      return '<a href="' + url + '" target="_blank" rel="noopener noreferrer">' + text + '</a>';
    });
    s = s.replace(/\*\*([^*\n]+)\*\*/g, '<strong>$1</strong>');
    s = s.replace(/~~([^~\n]+)~~/g, '<del>$1</del>');
    s = s.replace(/(^|[^*])\*([^*\n]+)\*(?!\*)/g, '$1<em>$2</em>');
    return s;
  }

  function fmtInline(raw) {
    var parts = String(raw == null ? '' : raw).split(/(`[^`\n]+`)/g);
    var out = '';
    for (var i = 0; i < parts.length; i++) {
      if (i % 2 === 1) {
        out += '<code class="inline">' + escapeHtml(parts[i].slice(1, -1)) + '</code>';
      } else {
        out += fmtText(parts[i]);
      }
    }
    return out;
  }

  function render(src) {
    var lines = String(src == null ? '' : src).replace(/\r\n?/g, '\n').split('\n');
    var html = [];
    var para = [];
    var list = null;
    var i = 0;

    function flushPara() {
      if (para.length) {
        html.push('<p>' + para.map(fmtInline).join('<br>') + '</p>');
        para = [];
      }
    }
    function flushList() {
      if (list) {
        var items = list.items.map(function (it) { return '<li>' + fmtInline(it) + '</li>'; }).join('');
        html.push('<' + list.tag + '>' + items + '</' + list.tag + '>');
        list = null;
      }
    }

    while (i < lines.length) {
      var line = lines[i];
      var fence = line.match(/^\s*```(.*)$/);
      if (fence) {
        flushPara();
        flushList();
        var lang = fence[1].trim();
        var buf = [];
        i++;
        while (i < lines.length && !/^\s*```\s*$/.test(lines[i])) {
          buf.push(lines[i]);
          i++;
        }
        if (i < lines.length) i++;
        html.push('<pre><code' + (lang ? ' data-lang="' + escapeHtml(lang) + '"' : '') + '>' + escapeHtml(buf.join('\n')) + '</code></pre>');
        continue;
      }
      if (/^\s*$/.test(line)) {
        flushPara();
        flushList();
        i++;
        continue;
      }
      var head = line.match(/^(#{1,4})\s+(.*)$/);
      if (head) {
        flushPara();
        flushList();
        var level = Math.min(head[1].length + 2, 5);
        html.push('<h' + level + '>' + fmtInline(head[2]) + '</h' + level + '>');
        i++;
        continue;
      }
      var quote = line.match(/^\s*>\s?(.*)$/);
      if (quote) {
        flushPara();
        flushList();
        html.push('<blockquote>' + fmtInline(quote[1]) + '</blockquote>');
        i++;
        continue;
      }
      var ul = line.match(/^\s*[-*+]\s+(.*)$/);
      if (ul) {
        flushPara();
        if (!list || list.tag !== 'ul') {
          flushList();
          list = { tag: 'ul', items: [] };
        }
        list.items.push(ul[1]);
        i++;
        continue;
      }
      var ol = line.match(/^\s*\d+[.)]\s+(.*)$/);
      if (ol) {
        flushPara();
        if (!list || list.tag !== 'ol') {
          flushList();
          list = { tag: 'ol', items: [] };
        }
        list.items.push(ol[1]);
        i++;
        continue;
      }
      if (list) flushList();
      para.push(line);
      i++;
    }
    flushPara();
    flushList();
    return html.join('');
  }

  function strip(src) {
    var t = String(src == null ? '' : src);
    t = t.replace(/```[^\n]*\n?([\s\S]*?)```/g, '$1');
    t = t.replace(/```/g, '');
    t = t.replace(/`([^`]*)`/g, '$1');
    t = t.replace(/\[([^\]]*)\]\([^)]*\)/g, '$1');
    t = t.replace(/(\*\*|__|~~)/g, '');
    t = t.replace(/\*([^*\n]+)\*/g, '$1');
    t = t.replace(/_[^_\n]+_/g, function (m) { return m.slice(1, -1); });
    t = t.replace(/^\s*#{1,6}\s+/gm, '');
    t = t.replace(/^\s*>\s?/gm, '');
    t = t.replace(/^\s*[-*+]\s+/gm, '');
    t = t.replace(/^\s*\d+[.)]\s+/gm, '');
    return t;
  }

  global.MiniMD = { render: render, strip: strip, escapeHtml: escapeHtml };
})(window);
