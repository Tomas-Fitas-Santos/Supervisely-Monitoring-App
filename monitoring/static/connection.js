/* Credentials stay in this component and a direct connection request, outside SDK widget state. */
Vue.component('nightjar-connection', {
  props: ['connection'],
  data() { return { server: this.connection.server_address || 'https://app.supervisely.com',
    userId: '',
    token: '', busy: false, editing: false, error: '' }; },
  methods: {
    async connect() {
      if (this.busy) return;
      this.busy = true; this.error = '';
      try {
        const url = new URL('./connection', window.location.href);
        const response = await fetch(url, {method: 'POST', credentials: 'same-origin',
          headers: {'Content-Type': 'application/json'}, body: JSON.stringify({server: this.server, token: this.token, user_id:this.userId})});
        const result = await response.json();
        if (!response.ok || !result.ok) throw new Error(result.message || 'Connection failed.');
        this.editing = false; this.$emit('connected');
      } catch (e) { this.error = e.message; }
      finally { this.token = ''; this.busy = false; }
    }
  },
  template: `<section class="nj-panel nj-connection">
    <template v-if="connection.connected && !editing"><div class="nj-section-heading">
      <div><h2>Connected to Supervisely</h2><p class="nj-muted">{{ connection.login || 'Your account' }} · {{ connection.server_address }}</p></div>
      <button @click="editing = true">Update connection</button></div></template>
    <template v-else><h2>Sign in with Supervisely</h2><p>Use your own Supervisely account. The event admin manages setup; monitors review their assigned teams.</p><p class="nj-muted">Enter your User ID and personal API token from your Supervisely profile. Your login lasts up to 12 hours in this browser.</p>
      <div class="nj-form-row"><label>Supervisely server<input v-model="server" type="url" autocomplete="url"></label>
        <label>Supervisely User ID<input v-model="userId" type="number" min="1" step="1" autocomplete="off"></label>
        <label>Your personal API token<input v-model="token" type="password" autocomplete="new-password" @keyup.enter="connect"></label></div>
      <div class="nj-actions"><button class="nj-primary" @click="connect" :disabled="busy || !server.trim() || !userId.trim() || !token.trim()">Sign in</button>
        <button v-if="connection.connected" @click="editing = false; token = ''" :disabled="busy">Cancel</button></div>
      <p v-if="error" class="nj-caution" role="alert">{{ error }}</p></template>
  </section>`
});
