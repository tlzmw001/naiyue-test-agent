export type DeliveryAction='branch'|'merge'|'discard';
export interface DeliveryCommand { operation_id:string;action:DeliveryAction;branch?:string|null }
export interface DeliveryOperation { operation_id:string;result_id:string;command:DeliveryCommand;state:string;result:unknown;error_code:string|null;created_at:string }
export interface DeliveryOptions {
  discarded:boolean;actions:Record<DeliveryAction,{available:boolean;reason?:string|null}>;
  operations:DeliveryOperation[];
}
