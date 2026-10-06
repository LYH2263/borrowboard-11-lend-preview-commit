<template>
  <div class="split">
    <section class="pane">
      <h2>可借物</h2>
      <div v-for="i in board.available" :key="i.id" class="item">
        <strong>{{ i.title }}</strong>
        <div class="muted">物主 {{ i.owner || '—' }}</div>
        <template v-if="!tickets[i.id]">
          <input v-model="forms[i.id].borrower" placeholder="借用人" />
          <input v-model="forms[i.id].due_date" placeholder="应还日 YYYY-MM-DD" />
          <button @click="preview(i.id)">预演</button>
        </template>
        <template v-else>
          <div class="muted">
            预演票：将占用「{{ tickets[i.id].title }}」 · 借用人 {{ tickets[i.id].borrower }} · 应还 {{ tickets[i.id].due_date }}
          </div>
          <button @click="confirm(i.id)">确认借出</button>
          <button class="ghost" @click="cancel(i.id)">取消</button>
        </template>
        <div v-if="errors[i.id]" class="error">{{ errors[i.id] }}</div>
      </div>
    </section>
    <section class="pane">
      <h2>在借 / 逾期</h2>
      <div v-for="l in [...board.overdue, ...board.active]" :key="l.id" class="item" :class="{ overdue: l.overdue }">
        <strong>{{ l.title }}</strong> → {{ l.borrower }}
        <div class="muted">应还 {{ l.due_date }} {{ l.overdue ? '· 逾期' : '' }}</div>
        <button @click="ret(l.id)">归还</button>
      </div>
    </section>
  </div>
</template>
<script setup>
import { inject, reactive, watch } from 'vue'
import { api } from '../api'
const board = inject('board')
const reload = inject('reloadBoard')
const forms = reactive({})
const tickets = reactive({})
const errors = reactive({})
watch(board, (b) => {
  for (const i of (b.available || [])) {
    if (!forms[i.id]) forms[i.id] = { borrower: '邻居', due_date: '2026-12-31' }
  }
}, { immediate: true, deep: true })
async function preview(id) {
  errors[id] = ''
  try {
    // 预演只读：可借栏与顶部细条都不动
    tickets[id] = await api('/items/' + id + '/lend/preview', { method: 'POST', body: JSON.stringify(forms[id]) })
  } catch (e) {
    delete tickets[id]
    errors[id] = e.message
  }
}
async function confirm(id) {
  errors[id] = ''
  const t = tickets[id]
  try {
    await api('/items/' + id + '/lend/confirm', {
      method: 'POST',
      body: JSON.stringify({ borrower: t.borrower, due_date: t.due_date }),
    })
    delete tickets[id]
    await reload()
  } catch (e) {
    // 确认失败：服务端未落任何写入，重拉后可借数与预演前一致
    delete tickets[id]
    errors[id] = e.message
    await reload()
  }
}
function cancel(id) {
  delete tickets[id]
  errors[id] = ''
}
async function ret(id) {
  await api('/loans/' + id + '/return', { method: 'POST', body: '{}' })
  await reload()
}
</script>
