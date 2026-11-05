/* Credentials stay in this component and a direct connection request, outside SDK widget state. */
Vue.component('nightjar-connection', {
  props: ['connection'],
  data() { return { server: this.connection.server_address || 'https://app.supervisely.com',
    userId: this.connection.user_id ? String(this.connection.user_id) : '',
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
    <template v-else><h2>Connect to Supervisely</h2><p class="nj-muted">Use your own Supervisely User ID and personal API token. We verify that the token belongs to this ID. The event team and workspace already exist.</p><p v-if="connection.pilot_email">For this live sample demo, connect the API token belonging to <strong>{{connection.pilot_email}}</strong>. Emails in the sample roster do not authenticate accounts.</p>
      <div class="nj-form-row"><label>Supervisely server<input v-model="server" type="url" autocomplete="url"></label>
        <label>Supervisely User ID<input v-model="userId" type="number" min="1" step="1" autocomplete="off"></label>
        <label>Your personal API token<input v-model="token" type="password" autocomplete="new-password" @keyup.enter="connect"></label></div>
      <div class="nj-actions"><button class="nj-primary" @click="connect" :disabled="busy || !server.trim() || !userId.trim() || !token.trim()">Connect account</button>
        <button v-if="connection.connected" @click="editing = false; token = ''" :disabled="busy">Cancel</button></div>
      <p v-if="error" class="nj-caution" role="alert">{{ error }}</p></template>
  </section>`
});
