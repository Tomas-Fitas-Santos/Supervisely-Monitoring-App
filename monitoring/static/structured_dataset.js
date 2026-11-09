/* Same verified dataset import UX for DEMO and PRODUCTION.
 * Browser file selection only stages into this monitoring app; the second,
 * explicit confirmation uploads the reviewed sources into Supervisely.
 */
Vue.component('event-structured-upload', {
  props: ['view','form','post','locked'],
  data() {
    return {mode:'folder',sourceName:'Bird Annotation Dataset',
      files:[],fps:12,metaText:'',uploadIds:[],preview:null,busy:false,
      progress:'',error:'',confirmed:false};
  },
  computed: {
    ready() {return !!this.view.event_settings?.single_team && !!this.view.event_settings?.workspace_id;},
    totalBytes() {return this.files.reduce((n,f)=>n+f.size,0);},
    selectionLabel() {
      return this.mode==='zip' ? 'ZIP containing the named dataset folder'
        : 'Dataset folder containing species / video title / frames';
    }
  },
  methods: {
    newSelection() {this.uploadIds=[];this.preview=null;this.progress='';this.error='';},
    chooseMode(mode) {if(this.busy)return;this.mode=mode;this.files=[];this.newSelection();},
    changeFiles(e) {
      this.newSelection();
      this.files=Array.from(e.target.files||[]);
      if(this.mode==='folder'){
        // Finder and Windows Explorer may add desktop-only metadata.
        this.files=this.files.filter(f=>{
          const parts=(f.webkitRelativePath||f.name).split('/');
          const leaf=parts[parts.length-1];
          return !parts.includes('__MACOSX') && !['.DS_Store','Thumbs.db'].includes(leaf)
            && !leaf.startsWith('._');
        });
      }
      if(this.mode==='zip'){
        if(this.files.length!==1||!/\.zip$/i.test(this.files[0].name)){
          this.error='Select one .zip file containing the named dataset folder.';
          this.files=[];return;
        }
        if(this.sourceName==='Bird Annotation Dataset')
          this.sourceName=this.files[0].name.replace(/\.zip$/i,'');
      } else {
        if(this.files.some(f=>!/^\S.*\.(jpe?g|png|bmp|webp|tiff?)$/i.test(f.name)
          || !f.webkitRelativePath)){
          this.error='Select the entire dataset folder. Only video frame image files are supported.';
          this.files=[];return;
        }
        if(this.files.length&&this.sourceName==='Bird Annotation Dataset')
          this.sourceName=this.files[0].webkitRelativePath.split('/')[0];
      }
    },
    async metaChanged(e) {
      this.error='';
      try {
        const file=e.target.files?.[0];
        this.metaText=file?await file.text():'';
        if(this.metaText)JSON.parse(this.metaText);
      } catch (_) {this.error='Choose a valid Supervisely meta.json file.';}
    },
    async request(operation, payload) {
      Object.assign(this.form,payload,{setup_action:operation});
      try{
        const result=await this.post('/setup/action');
        if(result?.ok===false||this.view.error)
          throw new Error(this.view.message||'Dataset operation failed.');
        return result;
      }finally{
        delete this.form.chunk;
      }
    },
    async stageFile(file, position) {
      const uid=crypto.randomUUID().replace(/-/g,'');
      const rel=this.mode==='zip'?file.name:file.webkitRelativePath;
      for(let offset=0;offset<file.size;offset+=4*1024*1024){
        const portion=file.slice(offset,offset+4*1024*1024);
        const b64=await new Promise((resolve,reject)=>{
          const reader=new FileReader();
          reader.onload=()=>resolve(reader.result.split(',')[1]);
          reader.onerror=()=>reject(new Error('Cannot read '+file.name));
          reader.readAsDataURL(portion);
        });
        this.progress='Staging '+(position+1)+' / '+this.files.length+
          ' files · '+Math.floor((offset+portion.size)/file.size*100)+'% of current file';
        await this.request('chunk',{
          upload_id:uid,filename:file.name,size:file.size,
          offset,chunk:b64,relative_path:rel
        });
      }
      return uid;
    },
    async inspect() {
      if(this.busy||this.locked||!this.ready||!this.files.length)return;
      this.busy=true;this.error='';this.preview=null;this.uploadIds=[];
      try{
        for(let i=0;i<this.files.length;i++)
          this.uploadIds.push(await this.stageFile(this.files[i],i));
        this.progress='Validating complete video sequences and archive integrity…';
        await this.request('media_staged_preview',{
          upload_ids:this.uploadIds,source_mode:this.mode
        });
        this.preview=JSON.parse(JSON.stringify(this.view.media_preview));
        if(!this.preview||this.preview.mode!==this.mode||!this.preview.hash)
          throw new Error('A verified dataset preview was not returned.');
        this.progress='Dataset validated. No files have been uploaded to Supervisely yet.';
      }catch(error){this.error=error.message||'Could not inspect dataset.';this.preview=null;}
      finally{this.busy=false;}
    },
    async confirm() {
      if(this.busy||this.locked||!this.preview||!this.sourceName.trim())return;
      this.busy=true;this.error='';
      try{
        const meta=this.metaText.trim()?JSON.parse(this.metaText):null;
        this.progress='Encoding complete videos and uploading verified sources to Supervisely…';
        await this.request('structured_upload',{
          operation_id:crypto.randomUUID().replace(/-/g,''),
          upload_ids:this.uploadIds,source_mode:this.mode,
          manifest_hash:this.preview.hash,
          archive_sha256:this.preview.archive_sha256,
          name:this.sourceName.trim(),fps:Number(this.fps),meta
        });
        this.progress='Dataset uploaded to the existing Supervisely workspace.';
        this.files=[];this.uploadIds=[];this.preview=null;
        this.$emit('uploaded');
      }catch(error){
        this.error=error.message||'Import failed. Inspect operation history before retrying.';
        this.progress='Upload was not confirmed. Do not restart an uncertain native operation.';
      }finally{this.busy=false;}
    }
  },
  template:`<section class="nj-panel nj-structured-upload">
    <h3>Upload event dataset</h3>
    <p class="nj-muted">Both DEMO and PRODUCTION accept an entire dataset folder or
      a ZIP containing that same folder. Under the dataset root, use species/morphology
      folders, then a named folder for each video containing its ordered frames.</p>
    <div class="nj-upload-modes">
      <button :class="{selected:mode==='folder'}" @click="chooseMode('folder')" :disabled="busy">Dataset folder</button>
      <button :class="{selected:mode==='zip'}" @click="chooseMode('zip')" :disabled="busy">ZIP archive</button>
    </div>
    <p v-if="!ready" class="nj-caution">Set the pre-existing team and source workspace IDs in Settings first.</p>
    <label class="nj-field">{{selectionLabel}}
      <input v-if="mode==='folder'" type="file" multiple webkitdirectory directory
        @change="changeFiles" :disabled="busy||locked" aria-label="Choose dataset folder">
      <input v-else type="file" accept=".zip,application/zip,application/x-zip-compressed"
        @change="changeFiles" :disabled="busy||locked" aria-label="Choose dataset ZIP">
    </label>
    <p class="nj-muted">{{files.length}} files selected · {{(totalBytes/1048576).toFixed(1)}} MiB</p>
    <div class="nj-form-row">
      <label>Supervisely dataset name
        <input v-model="sourceName" maxlength="180" :disabled="busy||locked">
      </label>
      <label>Video frames per second
        <input type="number" v-model.number="fps" min="1" max="60" step="1" :disabled="busy||locked">
      </label>
    </div>
    <p class="nj-muted">Destination: Team {{view.event_settings?.team_id||'—'}} ·
      Workspace {{view.event_settings?.workspace_id||'—'}}. Frame folders will
      become complete video files before the native upload; original frame paths
      remain in the dataset manifest.</p>
    <details><summary>Optional annotation metadata</summary>
      <label class="nj-field">Supervisely meta.json
        <input type="file" accept=".json,application/json" @change="metaChanged" :disabled="busy||locked">
      </label>
    </details>
    <div v-if="error" class="nj-message nj-error" role="alert">{{error}}</div>
    <div class="nj-actions">
      <button @click="inspect" class="nj-primary"
        :disabled="busy||locked||!ready||!files.length||!!error">1. Stage and inspect dataset</button>
    </div>
    <div v-if="preview" class="nj-panel">
      <h4>Validated dataset — confirm before uploading</h4>
      <div class="nj-metrics nj-three-metrics">
        <div><span>Bird categories</span><strong>{{preview.categories}}</strong></div>
        <div><span>Complete videos</span><strong>{{preview.videos}}</strong></div>
        <div><span>Video frames</span><strong>{{preview.frames}}</strong></div>
      </div>
      <p>Categories: {{preview.categories}} ·
        Total unpacked: {{(preview.unpacked_bytes/1048576).toFixed(1)}} MiB</p>
      <p v-for="v in preview.video_examples||[]" :key="v.category+'/'+v.name">
        {{v.category}} / {{v.name}} — {{v.frames}} ordered frames
      </p>
      <button class="nj-primary" @click="confirm"
        :disabled="busy||locked||!sourceName.trim()||!fps||fps<1||fps>60">
        2. Confirm upload to Supervisely
      </button>
    </div>
    <p v-if="progress" role="status" class="nj-muted">{{progress}}</p>
    <p class="nj-muted">Validation and staging stay in the monitoring app.
      Supervisely resources are created only after explicit confirmation.</p>
  </section>`
});
