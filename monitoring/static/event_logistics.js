/* Reception desk: audited per-person pizza/drink issuance and redemption. */
Vue.component('event-logistics', {
  props: ['view', 'form', 'post', 'locked'],
  data() {
    return {busy:false, error:'', query:'', quantity:1, notes:{}, timer:null};
  },
  computed: {
    logistics() {return this.view.logistics||{teams:[],summary:{}};},
    summary() {return this.logistics.summary||{};},
    teams() {
      const q=this.query.trim().toLowerCase();
      return (this.logistics.teams||[]).filter(t=>!q||
        [t.name,t.table_number,...(t.members||[]).flatMap(p=>[p.name,p.email])]
          .some(v=>String(v||'').toLowerCase().includes(q)));
    }
  },
  methods: {
    key(t,p,kind) {return t.id+'/'+p.email+'/'+kind;},
    async act(action,team=null,person=null,kind=null) {
      if(this.busy||this.locked)return;
      this.busy=true;this.error='';
      const key=team&&person&&kind?this.key(team,person,kind):'';
      Object.assign(this.form,{
        logistics_action:action,
        operation_id:crypto.randomUUID().replace(/-/g,''),
        registration_id:team?.id||'',
        member_email:person?.email||'',
        ticket_kind:kind||'',
        quantity:Number(this.quantity),
        ticket_note:key?(this.notes[key]||'').trim():''
      });
      try {
        const reply=await this.post('/logistics/action');
        if(reply?.ok===false||this.view.error)
          throw new Error(this.view.message||'Reception action failed.');
        if(key&&(action==='void_issue'||action==='undo_redemption'))this.$delete(this.notes,key);
      } catch(e) {
        this.error=e.message||'Could not update ticket ledger.';
      } finally {this.busy=false;}
    }
  },
  mounted() {
    this.act('refresh');
    this.timer=setInterval(()=>{
      if(!document.hidden&&!this.busy)this.act('refresh');
    },60000);
  },
  beforeDestroy(){clearInterval(this.timer);},
  template:`<section class="nj-management nj-logistics">
    <div class="nj-section-heading"><div><h2>Food &amp; Drinks — Reception</h2>
      <p class="nj-muted">Issue tickets when handing them to participants. Redeem a ticket
        when the participant collects food or a drink at reception. All operations
        record the staff member and time. DEMO and PRODUCTION use the same workflow.</p>
      </div><button @click="act('refresh')" :disabled="busy||locked">Refresh tickets</button></div>
    <p v-if="error" role="alert" class="nj-message nj-error">{{error}}</p>
    <div class="nj-metrics nj-three-metrics">
      <div><span>Participants</span><strong>{{summary.participants||0}}</strong></div>
      <div><span>Pizza issued / redeemed</span>
        <strong>{{summary.pizza_issued||0}} / {{summary.pizza_redeemed||0}}</strong></div>
      <div><span>Drinks issued / redeemed</span>
        <strong>{{summary.drink_issued||0}} / {{summary.drink_redeemed||0}}</strong></div>
    </div>
    <div class="nj-panel">
      <div class="nj-form-row">
        <label>Find a participant, team or table<input v-model="query" placeholder="Search reception list"></label>
        <label>Tickets per action<input type="number" min="1" max="10" v-model.number="quantity"></label>
      </div>
      <div class="nj-table-scroll"><table class="nj-table">
        <thead><tr><th>Team / Table</th><th>Participant</th>
          <th>Pizza tickets</th><th>Drink tickets</th></tr></thead>
        <tbody><template v-for="team in teams">
          <tr v-for="person in team.members" :key="team.id+'-'+person.email">
            <td><strong>{{team.name}}</strong>
              <small class="nj-small-text">Table {{team.table_number||'not assigned'}}</small></td>
            <td>{{person.name}}<small class="nj-small-text">{{person.email}}</small></td>
            <td v-for="kind in ['pizza','drink']" :key="kind">
              <strong>{{person[kind].redeemed}} / {{person[kind].issued}} redeemed</strong>
              <div class="nj-inline-actions">
                <button @click="act('issue',team,person,kind)"
                  :disabled="busy||locked||!team.can_issue||quantity<1||quantity>10">Issue</button>
                <button class="nj-primary" @click="act('redeem',team,person,kind)"
                  :disabled="busy||locked||quantity<1||quantity>10||
                    person[kind].issued-person[kind].redeemed<quantity">Redeem</button>
              </div>
              <details v-if="team.can_issue"><summary>Correct an entry</summary>
                <input :value="notes[key(team,person,kind)]||''"
                  @input="$set(notes,key(team,person,kind),$event.target.value)"
                  maxlength="500" placeholder="Reason required">
                <div class="nj-inline-actions">
                  <button @click="act('void_issue',team,person,kind)"
                    :disabled="busy||locked||!(notes[key(team,person,kind)]||'').trim()||
                      person[kind].issued-person[kind].redeemed<quantity">Void issued</button>
                  <button @click="act('undo_redemption',team,person,kind)"
                    :disabled="busy||locked||!(notes[key(team,person,kind)]||'').trim()||
                      person[kind].redeemed<quantity">Undo redemption</button>
                </div>
              </details>
            </td>
          </tr>
        </template></tbody>
      </table></div>
      <p v-if="!teams.length" class="nj-muted">No matching participants. Import
        the registration roster and assign competition teams first.</p>
      <p class="nj-muted">Reception staff can redeem tickets for any participant.
        Only the assigned monitor or organiser can issue or correct tickets.
        A redemption cannot exceed the number issued.</p>
    </div>
  </section>`
});
