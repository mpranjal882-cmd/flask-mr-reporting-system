// validation.js

function validateLogin(){
  const u = document.getElementById('username').value.trim();
  const p = document.getElementById('password').value.trim();
  if(!u || !p){
    alert('Please enter username and password.');
    return false;
  }
  return true;
}

function validateCreateUser(){
  // Basic checks on create MR/admin form
  const forms = document.querySelectorAll('form');
  // This runs on submit, fields required in HTML already.
  return true;
}

function validateReportForm(){
  const doc = document.getElementById('doctor_name') ? document.getElementById('doctor_name').value.trim() : '';
  const hosp = document.getElementById('hospital_name') ? document.getElementById('hospital_name').value.trim() : '';
  const d = document.getElementById('visit_date') ? document.getElementById('visit_date').value : '';
  if(!doc || !hosp || !d){
    alert('Doctor name, Hospital and Date are required.');
    return false;
  }
  return true;
}