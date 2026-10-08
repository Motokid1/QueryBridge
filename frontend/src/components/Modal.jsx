import React,{useEffect,useRef,useId} from 'react';
import {X} from 'lucide-react';
export default function Modal({title,children,onClose,busy=false}){
 const ref=useRef(),id=useId();
 useEffect(()=>{ref.current.showModal();return()=>ref.current?.close()},[]);
 return <dialog ref={ref} aria-labelledby={id} onCancel={e=>{e.preventDefault();if(!busy)onClose()}}><div className="modal-heading"><h2 id={id}>{title}</h2><button className="icon-button" aria-label="Close dialog" disabled={busy} onClick={onClose}><X size={20}/></button></div>{children}</dialog>;
}
