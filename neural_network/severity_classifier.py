import cv2
import numpy as np
import torch
import torch.nn as nn
from torchvision.models import mobilenet_v2

class SeverityClassifier:
    def __init__(self, model_weights_path: str, device: str = None):
        """
        Inizializza l'Engine di Computer Vision.
        """
        # 1. Configurazione Hardware
        self.device = device if device else ("cuda" if torch.cuda.is_available() else "cpu")
        
        # 2. Statistiche di Normalizzazione ImageNet (Le stesse usate in training)
        self.mean = np.array([0.485, 0.456, 0.406], dtype=np.float32)
        self.std = np.array([0.229, 0.224, 0.225], dtype=np.float32)
        self.classes = {0: "0_Lieve", 1: "1_Grave"}
        
        # 3. Caricamento Modello
        self.model = self._load_model(model_weights_path)

    def _load_model(self, weights_path: str) -> nn.Module:
        """Ricrea l'architettura e inietta i pesi addestrati."""
        model = mobilenet_v2(weights=None) # Partiamo da un'architettura vuota
        in_features = model.classifier[1].in_features
        model.classifier[1] = nn.Linear(in_features, 2) # Ricreiamo la nostra testa binaria
        
        # Carichiamo i pesi dal file .pth
        state_dict = torch.load(weights_path, map_location=self.device)
        model.load_state_dict(state_dict)
        
        model.to(self.device)
        model.eval() # FONDAMENTALE: Spegne Dropout e BatchNorm per l'inferenza
        return model

    def _preprocess_opencv(self, image_path: str) -> torch.Tensor:
        """
        Replica esatta della pipeline torchvision.transforms usando OpenCV.
        """
        # A. Lettura immagine (OpenCV legge in BGR)
        img = cv2.imread(image_path)
        if img is None:
            raise ValueError(f"Impossibile leggere l'immagine al percorso: {image_path}")
            
        # B. Conversione BGR -> RGB (PyTorch si aspetta RGB)
        img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        
        # C. Resize esatto a 224x224
        img = cv2.resize(img, (224, 224))
        
        # D. Scala pixel da 0-255 a 0.0-1.0 (Come ToTensor)
        img = img.astype(np.float32) / 255.0
        
        # E. Normalizzazione Statistica ImageNet
        img = (img - self.mean) / self.std
        
        # F. Cambio formato: da [Altezza, Larghezza, Canali] a [Canali, Altezza, Larghezza]
        img = np.transpose(img, (2, 0, 1))
        
        # G. Conversione in Tensore e aggiunta della "Batch Dimension" [1, C, H, W]
        tensor = torch.from_numpy(img).unsqueeze(0).to(self.device)
        return tensor

    def evaluate_severity(self, image_path: str) -> dict:
        """
        Funzione pubblica esposta all'Orchestratore (es. FastAPI).
        """
        try:
            input_tensor = self._preprocess_opencv(image_path)
            
            with torch.no_grad(): # Nessun calcolo di gradienti, massima velocità
                outputs = self.model(input_tensor)
                # Applichiamo Softmax per trasformare l'output grezzo in Probabilità (0-1)
                probabilities = torch.nn.functional.softmax(outputs[0], dim=0)
            
            # Estrazione dei risultati
            confidence, predicted_idx = torch.max(probabilities, 0)
            class_name = self.classes[predicted_idx.item()]
            
            # Output Standardizzato
            return {
                "status": "success",
                "prediction": class_name,
                "confidence": round(float(confidence.item()), 4),
                "details": {
                    "Lieve_prob": round(float(probabilities[0].item()), 4),
                    "Grave_prob": round(float(probabilities[1].item()), 4)
                }
            }
            
        except Exception as e:
            return {"status": "error", "message": str(e)}